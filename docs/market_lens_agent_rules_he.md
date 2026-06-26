# Market Lens Agent - הסבר מלא על בחירת מניות, ניתוח, חוקי כניסה וניהול תיק

מסמך זה מסביר לעומק איך מערכת Market Lens בוחרת מניות לסריקה, איך היא מנתחת כל מניה, איך היא מחליטה אם יש Setup, ומהם החוקים שמאפשרים או חוסמים כניסה לטרייד סימולטיבי.

המערכת היא מערכת Paper Trading בלבד. היא לא מחוברת לברוקר, לא שולחת פקודות אמיתיות, ולא משתמשת בכסף אמיתי.

## תקציר ארכיטקטורה

המערכת בנויה מכמה שכבות:

1. Smart Universe - בחירת מניות איכותיות לסריקה.
2. Basic Filters - סינון ראשוני לפי איכות, נזילות ותנודתיות.
3. Sector Health - בדיקת מצב הסקטור.
4. Technical Scanner - ניתוח טכני של כל מניה.
5. Setup Detection - זיהוי תבניות מסחר.
6. Professional Context - דירוג איכות נוסף לפי שוק, מגמה, נפח ואירועים.
7. Agent Risk Layer - חוקי כניסה, סיכון, קורלציה, חשיפה ו-R/R נטו.
8. Portfolio State - ניהול תיק סימולטיבי, מזומן, פוזיציות וסיכון.
9. Position Monitor - ניטור TP/SL לאחר כניסה.
10. Dashboard / Excel / JSONL - תיעוד מלא של כל החלטה.

חשוב: סריקה ידנית של משתמש והסריקה של ה-Agent משתמשות באותו מנוע ניתוח ואותה שכבת החלטות אסטרטגית. ההבדל הוא שה-Agent גם מנהל תיק סימולטיבי, היסטוריית פוזיציות, Excel, GitHub Actions וניטור TP/SL.

## מקורות המניות לסריקה

ה-Agent לא סורק רשימה קטנה וקבועה כברירת מחדל. הוא בונה Smart Universe רחב ודינמי.

מקורות ה-Universe:

- S&P 500
- Nasdaq-100
- Russell 1000
- Russell 3000
- רשימות הסקטורים הקיימות ב-Dropdowns של האפליקציה
- fallback רחב מ-Nasdaq Screener אם נתוני Russell לא זמינים

ברירת המחדל היא מקור רחב:

```text
MARKET_LENS_SMART_SOURCE=broad
```

המערכת מנסה למשוך רשימות חיצוניות מהרשת. אם מקור חיצוני לא זמין, היא משתמשת ב-curated fallback מתוך הרשימות המובנות באפליקציה.

## סינון ראשוני של מניות

לפני שמניה נכנסת לניתוח, היא חייבת לעבור סינון בסיסי.

חוקי סינון עיקריים:

- רק common US equities.
- לא ETFs.
- לא warrants.
- לא rights.
- לא units.
- לא preferred shares.
- לא notes או bonds.
- לא acquisition corp / blank-check vehicles.
- מחיר מינימלי: 10 דולר.
- Average Dollar Volume ל-20 ימים: לפחות 100 מיליון דולר.
- ATR% מינימלי: 1.2%.
- ATR% מקסימלי: 8.0%.
- Market Cap מינימלי במקור הרחב: 1 מיליארד דולר.
- Volume מינימלי במקור הרחב: 250,000 מניות.
- סקטור Weak לא נכנס כמועמד חדש לסריקה.

הסיבה לסינונים:

- להימנע ממניות לא נזילות.
- להימנע ממניות Penny / רעש גבוה.
- להימנע ממכשירים שאינם מניות רגילות.
- לשמור על מניות שיש להן מספיק תנועה למסחר Swing.
- לא להכניס מניות מסקטור חלש רק בגלל שיש תבנית טכנית רגעית.

## כמות מניות בסריקה

ברירת המחדל הנוכחית בענן:

```text
MARKET_LENS_AGENT_UNIVERSE_TARGET=100
MARKET_LENS_AGENT_UNIVERSE_POOL=100
MARKET_LENS_AGENT_UNIVERSE_MAX_POOL=300
MARKET_LENS_AGENT_MAX_PER_SECTOR=15
MARKET_LENS_AGENT_SCAN_BATCH_SIZE=20
```

המשמעות:

- המטרה היא לסרוק עד 100 מניות בריצה.
- מניות נבחרות מתוך pool רחב יותר.
- יש תקרת פיזור לפי סקטורים.
- הסריקה מתבצעת בבאצ'ים של 20 כדי לא להעמיס על Render / GitHub Actions / yfinance.

בנוסף:

- מניות ב-WATCH או WATCH_READY נשמרות למעקב עד 14 ימים.
- מניות WATCH לא אמורות לבוא על חשבון quota של מניות חדשות.
- מניות שנעשו SKIP מקבלות cooldown של 8 שעות כדי לא לסרוק שוב ושוב את אותן מניות חלשות באותו יום.
- פוזיציות פתוחות תמיד נשארות במעקב, גם אם הן לא נבחרו מחדש על ידי Smart Universe.

## Sector Health

לפני בחירת מניות, המערכת מחשבת מצב סקטורים.

לכל סקטור יש ETF מייצג:

- Technology - XLK
- Semiconductors - SMH
- Financials - XLF
- Healthcare - XLV
- Industrials - XLI
- Energy - XLE
- Consumer - XLY
- Communication Services - XLC
- Utilities - XLU
- Real Estate - XLRE
- Materials - XLB

חישוב Sector Health כולל:

- מחיר ETF מול EMA20.
- מחיר ETF מול EMA50.
- יחס EMA20 מול EMA50.
- שיפוע EMA50.
- תשואה חודשית.
- תשואה של 3 חודשים.
- Relative Strength מול SPY.
- Momentum של הסקטור.

ניקוד הסקטור:

```text
trend_score * 45%
relative_strength_score * 35%
momentum_score * 20%
```

סיווג:

- Strong: score >= 68
- Neutral: score >= 42
- Weak: score < 42

השפעה בפועל:

- Strong - יכול לקבל יותר מניות בסריקה ויכול לאפשר BUY אם שאר החוקים עוברים.
- Neutral - מקבל פחות מניות ודורש Setup איכותי יותר.
- Weak - לא מאפשר auto-buy; לרוב בכלל לא נכנס כמועמד חדש לסריקה.

## דירוג מניה ב-Smart Universe

אחרי הסינון הראשוני, כל מניה מקבלת Smart Score.

המדדים:

- Relative Strength מול SPY ו-QQQ.
- תשואה חודשית.
- תשואה של 3 חודשים.
- תשואה של 6 חודשים.
- Trend Score.
- Average Dollar Volume.
- ATR%.
- Sector Health.

חישוב Relative Strength:

```text
relative_strength = (RS מול SPY * 65%) + (RS מול QQQ * 35%)
```

Trend Score:

- מחיר מעל EMA20: +25
- מחיר מעל EMA50: +25
- מחיר מעל EMA200: +20
- EMA20 מעל EMA50: +20
- EMA50 עולה לעומת 10 ימים אחורה: +10
- אם תנאי נכשל, יש עונש שלילי לפי אותו סעיף.

Stock Score:

```text
RS Score * 32%
Trend Score * 30%
Momentum Score * 18%
Volume Score * 12%
Volatility Score * 8%
```

Final Smart Score:

```text
stock_score * 78% + sector_health_score * 22%
```

כלומר מניה לא נבחרת רק בגלל הגרף שלה. היא צריכה גם להיות בסקטור סביר או חזק.

## פיזור בין סקטורים

אחרי הדירוג, המערכת בוחרת סל מניות מגוון.

כללי quota:

- Strong sector - יכול לקבל quota גבוה יותר.
- Neutral sector - quota נמוך יותר.
- Weak sector - quota אפס למועמדים חדשים.

יש גם מנגנון rotation יומי לפי hash של תאריך + ticker. המטרה היא שלא בכל יום וריצה נקבל בדיוק אותן מניות, ועדיין נשמור על איכות.

## מה נבדק בכל מניה בסריקה הטכנית

עבור כל ticker, הסורק מוריד נתונים ומחשב:

- Daily candles.
- Hourly candles.
- Weekly candles.
- Current regular-session close.
- ATR.
- VWAP.
- EMA20.
- Weekly MA200.
- Volume Profile.
- POC - Point of Control.
- VAL - Value Area Low.
- VAH - Value Area High.
- HVN - High Volume Nodes.
- Fibonacci swing.
- Fib 61.8%.
- Fib 78.6%.
- Swing Low confluence.
- Market structure.
- Volume-price scenario.
- Relative Strength מול SPY.
- Earnings date אם יש setup.
- Extended-hours quote לצורכי מידע.

Extended-hours:

- המחיר של pre-market / after-hours מוצג למשתמש.
- הוא משמש להבנת מצב עדכני.
- הוא לא פותח BUY_SIMULATED לבד.
- כניסה דורשת confirmation של regular session כאשר `MARKET_LENS_ALLOW_BUY_OUTSIDE_REGULAR_HOURS=false`.

## טכניקות הסריקה הטכניות הקיימות במערכת

המערכת לא מסתמכת על אינדיקטור אחד. היא בונה תמונת מצב מכמה טכניקות טכניות, ואז מחברת אותן ל-Setup Score ולחוקי כניסה.

חשוב: אף טכניקה לא פותחת עסקה לבד. Fibonacci, VWAP, Volume Profile או Breakout הם רק שכבות ניתוח. BUY_SIMULATED דורש גם Market Regime, Sector Regime, R/R נטו, confirmation, סיכון, חשיפה וקורלציה.

### ATR - Average True Range

ATR הוא מדד התנודתיות המרכזי במערכת.

החישוב:

- משתמש ב-14 נרות יומיים כברירת מחדל.
- מחשב True Range לכל יום:
  - High - Low
  - abs(High - Previous Close)
  - abs(Low - Previous Close)
- לוקח את המקסימום מבין השלושה.
- מחליק את הסדרה ב-EWM לפי alpha = 1 / 14.

שימושים במערכת:

- סינון מניות לפי ATR%.
- קביעת proximity לרמות Volume Profile.
- קביעת proximity ל-VWAP.
- בניית Fibonacci zone סביב Fib 61.8.
- קביעת Stop מתחת לרמות טכניות.
- קביעת Targets מינימליים וסבירים.
- בדיקת Target feasibility.
- זיהוי אם יעד קרוב מדי או רחוק מדי.
- חישוב תנודתיות לניקוד Smart Universe.

ספי ATR% ב-Smart Universe:

```text
MIN_ATR_PCT = 1.2%
MAX_ATR_PCT = 8.0%
```

המשמעות:

- מתחת 1.2%: המניה עלולה להיות איטית מדי לסווינג.
- מעל 8%: המניה עלולה להיות תנודתית ורועשת מדי.

### Volume Profile

המערכת מחשבת Volume Profile על בסיס נרות Hourly.

החישוב:

- מחלקת את טווח המחירים ל-50 buckets.
- לכל bucket מחושב נפח לפי Volume.
- POC הוא bucket עם הנפח הגבוה ביותר.
- Value Area נבנית מה-buckets בעלי הנפח הגבוה ביותר עד שמצטברים ל-70% מהנפח.
- VAL הוא הגבול התחתון של Value Area.
- VAH הוא הגבול העליון של Value Area.
- HVN הם High Volume Nodes: buckets עם נפח לפחות 60% מנפח ה-POC, עד 3 רמות.
- HVN שקרוב מדי ל-POC, פחות מ-0.2 ATR, מסונן כדי לא לספור את אותה רמה פעמיים.

רמות Volume Profile:

- POC - Point of Control, אזור המחיר שבו עבר הכי הרבה נפח.
- VAL - Value Area Low, תחתית אזור הערך.
- VAH - Value Area High, תקרת אזור הערך.
- HVN - אזורי נפח גבוה משניים.

שימושים:

- Confluence ל-Fibonacci.
- תמיכה ל-Swing Low.
- יעד פוטנציאלי ל-Liquidity Trap.
- Target 2 או target structure.
- ניקוד Setup.
- זיהוי אם מחיר נמצא בתוך Value Area.

ספי קרבה:

```text
price vs POC / VAL / HVN <= 0.5 ATR
```

ניקוד:

- POC confluence: +25
- VAL confluence: +12
- HVN confluence: +8
- מחיר בתוך Value Area: -20

ההיגיון:

- POC/VAL/HVN מייצגים אזורים שבהם שחקנים גדולים פעלו בעבר.
- כאשר Fib / Swing / VWAP מתחברים לאותה רמה, ה-Setup איכותי יותר.
- מחיר בתוך Value Area מקבל עונש כי הוא פחות ברור כנקודת כניסה; הוא יכול להיות "באמצע הרעש".

### VWAP - Session VWAP

המערכת מחשבת Session VWAP מנרות Hourly.

החישוב:

- Typical Price = (High + Low + Close) / 3.
- VWAP מחושב רק ליום המסחר האחרון.
- החישוב מתאפס בתחילת כל יום.
- אם אין Volume, המערכת משתמשת ב-Close האחרון כ-fallback.

שימושים:

- זיהוי VWAP Reclaim Setup.
- Confluence ל-Fib 61.8.
- יעד ראשון ב-Fib setup אם VWAP מעל entry.
- יעד ראשון ב-Swing Volume אם VWAP מעל entry.
- ניקוד קרבה ל-VWAP.

סף קרבה:

```text
abs(price - VWAP) <= 0.3 ATR
```

ניקוד:

- VWAP proximity: +8
- Volume confirmed VWAP reclaim: +12

היגיון:

- VWAP משמש כקו "מחיר הוגן" תוך-יומי.
- Reclaim מעל VWAP יכול להראות שהקונים מחזירים שליטה.
- Touch בלבד לא מספיק. נדרש reclaim/close מעל VWAP בשכבת Entry Confirmation.

### Anchored VWAP

בנוסף ל-Session VWAP, קיימת בדיקת Anchored VWAP בהקשר של VWAP Reclaim.

החישוב:

- מסתכלת על עד 63 נרות יומיים אחרונים.
- מוצאת את ה-swing low הנמוך ביותר בחלון.
- מחשבת VWAP מהנקודה הזו ועד היום.

שימוש:

- לא משמש כ-trigger עצמאי.
- משמש כהקשר ל-VWAP Reclaim.
- VWAP Reclaim נחשב איכותי יותר אם המחיר מעל או קרוב ל-Anchored VWAP.

תנאי:

```text
current_price >= anchored_vwap - 0.3 ATR
```

אם המחיר מתחת ל-Anchored VWAP בצורה ברורה, ה-VWAP reclaim פחות איכותי.

### EMA20

המערכת מחשבת EMA20 על בסיס Daily candles.

שימושים:

- price_above_ema.
- Trend filter.
- ניקוד Setup.

ניקוד:

- מחיר מעל EMA20: +8
- מחיר מתחת EMA20: -15

היגיון:

- כניסה Long מעל EMA20 מקבלת תמיכה של מומנטום קצר.
- מחיר מתחת EMA20 מקבל עונש כי setup long בתוך חולשה קצרה פחות איכותי.

### Weekly MA200

המערכת מחשבת MA200 על בסיס Weekly candles.

שימושים:

- פילטר שוק רחב למניה עצמה.
- ניקוד setup.

ניקוד:

- מחיר מעל Weekly MA200: +10
- מחיר מתחת Weekly MA200: -25

היגיון:

- Weekly MA200 משמש הקשר מגמה ארוך.
- מניה מתחת MA200 שבועי מקבלת עונש משמעותי כי סווינג Long נגד מגמה ארוכה מסוכן יותר.

### Market Structure - HH/HL מול LH/LL

המערכת מזהה מבנה שוק לפי pivots יומיים.

החישוב:

- מוצאת pivot highs ו-pivot lows עם חלון של 5 נרות.
- בודקת את שני ה-pivot highs האחרונים ואת שני ה-pivot lows האחרונים.

סיווג:

- Uptrend: Higher High + Higher Low.
- Downtrend: Lower High + Lower Low.
- Ranging: כל מצב אחר.

ניקוד:

- Uptrend: +12
- Downtrend: -20
- Ranging: ללא תוספת.

היגיון:

- Long setup בתוך uptrend איכותי יותר.
- Long setup בתוך downtrend דורש זהירות, ולכן מקבל עונש.

### Pivot High / Pivot Low

המערכת משתמשת ב-pivots כדי לזהות מבנים:

- Fibonacci impulse.
- Breakout resistance.
- Swing low support.
- Market structure.

Pivot high:

- High של נר גבוה יותר מ-5 נרות לפניו ומ-5 נרות אחריו.

Pivot low:

- Low של נר נמוך יותר מ-5 נרות לפניו ומ-5 נרות אחריו.

המשמעות:

- Pivot הוא לא כל High/Low רגיל.
- הוא נקודת מבנה יחסית ברורה בתוך הגרף.

### Fibonacci Impulse + Fib 61.8

המערכת מזהה מהלך אימפולסיבי ואז מחשבת Fibonacci.

שלבים:

1. מוצאת pivot lows ו-pivot highs.
2. בונה impulse move מ-pivot low אל pivot high הבא אחריו.
3. impulse תקף רק אם גודל המהלך לפחות 2 ATR.
4. אם יש כמה impulses, הם מדורגים לפי:
   - 60% recency.
   - 40% move size.
5. נבחרים עד שני impulses מובילים.
6. מחושבות רמות:
   - Fib 38.2
   - Fib 50.0
   - Fib 61.8
   - Fib 78.6

חישוב הרמות:

```text
fib_382 = swing_high - 0.382 * range
fib_500 = swing_high - 0.500 * range
fib_618 = swing_high - 0.618 * range
fib_786 = swing_high - 0.786 * range
```

אזור קנייה סביב Fib 61.8:

```text
zone_low  = fib_618 - 0.25 ATR
zone_high = fib_618 + 0.25 ATR
```

המניה נחשבת near fib אם המחיר בתוך zone_low-zone_high.

שימושים:

- Fib 61.8 Confluence Buy Zone.
- Stop מתחת Fib 78.6.
- Target לפי Fib 50 / swing high.
- Confluence עם POC / VAL / VWAP.
- ניקוד proximity ל-Fib 61.8.

ניקוד:

- Fib 61.8 proximity: עד +20, לפי קרבה.
- Fib zone swept and reclaimed: +20.

היגיון:

- Fib 61.8 משמש כאזור pullback בתוך מהלך קודם.
- המערכת לא קונה רק בגלל Fib. היא דורשת confluence עם רמות נוספות ואישור כניסה.

### ICT/OTE-style Fib Sweep

בתוך Fib setup יש בדיקה האם אזור ה-Fib נשטף ונכבש מחדש.

הבדיקה:

- נר Hourly או Daily ירד מתחת ל-zone_low.
- אותו נר או נר רלוונטי נסגר חזרה מעל הרמה.

ניקוד:

```text
fib_zone_swept = +20
```

היגיון:

- sweep מתחת לאזור יכול להראות ניקוי נזילות.
- reclaim חזרה פנימה משפר את איכות ה-setup.

### Swing Low + Volume Support

המערכת מחפשת swing low שמתחבר לרמת Volume Profile.

השלבים:

1. מוצאים impulses תקפים.
2. לוקחים עד 3 swing lows אחרונים מתוך impulses תקפים.
3. בודקים האם swing low קרוב ל:
   - POC
   - VAL
   - HVN
4. קרבה נדרשת:

```text
abs(swing_low - volume_level) <= 0.25 ATR
```

5. בודקים אם המחיר הנוכחי קרוב ל-swing low:

```text
abs(current_price - swing_low) <= 0.3 ATR
```

6. בודקים sweep:
   - Hourly low מתחת swing_low וסגירה מעליו.
   - או Daily low מתחת swing_low וסגירה מעליו ב-10 ימים אחרונים.

אם המחיר מתחת ל-swing low ביותר מ-0.1 ATR ואין reclaim:

```text
accepted_below = true
```

וזה מקבל עונש.

שימוש:

- Swing Low + Volume Support Buy Zone.
- ניקוד Swing proximity.
- ניקוד Sweep.
- עונש אם המחיר התקבל מתחת לרמה.

ניקוד:

- Swing low proximity: +10
- Sweep and reclaim: +30
- Daily sweep bonus: +5
- Accepted below: -20

### Liquidity Sweep / Sweep And Reclaim

המערכת מחפשת wick מתחת לרמת תמיכה וסגירה חזרה מעליה.

סוגי sweep:

- Sweep מתחת VAL.
- Sweep מתחת swing low.
- Sweep מתחת Fib zone.

בדיקה Hourly:

```text
low < support AND close > support
```

בדיקה Daily:

```text
daily low < support AND daily close > support
```

ניקוד:

- Sweep and reclaim: +30
- Daily sweep bonus: +5

היגיון:

- Stop hunt / liquidity grab.
- אם המחיר חוזר מעל הרמה, זה יכול להיות סימן לכשל שבירה.

### Liquidity Trap

Liquidity Trap הוא Setup שמשתמש ב-Sweep And Reclaim סביב VAL/POC.

שני מצבים:

1. איכות גבוהה יותר:
   - המחיר swept below VAL.
   - המחיר reclaimed VAL.
   - POC מספק תמיכה מבנית.

2. איכות נמוכה יותר:
   - המחיר מתחת VAL, קרוב ל-POC.
   - ה-wipe עדיין בתהליך.

המערכת מחמירה עם ה-Setup הזה:

- ב-BEAR לא קונים.
- בסקטור WEAK לא קונים.
- צריך confirmation.
- אם אין reclaim חזק, זה WATCH ולא BUY.

### Breakout + Retest

המערכת מזהה resistance מפריצה קודמת.

שלבים:

1. מוצאת pivot highs ב-60 נרות אחרונים.
2. עבור כל pivot high, מחפשת Close מעל אותה רמת resistance.
3. בודקת האם הייתה פריצה אמיתית.
4. בודקת נפח ביום הפריצה:

```text
breakout_volume >= 1.5 * average_volume_20d
```

5. בודקת האם המחיר הנוכחי נמצא ב-retest zone:

```text
retest_low  = resistance - 1.0 ATR
retest_high = resistance + 0.5 ATR
```

6. בודקת שלא הייתה שבירה עמוקה אחרי הפריצה:

```text
invalidation_level = resistance - 1.5 ATR
אין close מתחת לרמה הזו אחרי הפריצה
```

Stop:

```text
stop_loss = resistance - 1.5 ATR
```

Target 1:

- קודם לפי nearest structure target.
- fallback לפי measured move:

```text
resistance + (resistance - stop_loss)
```

Target 2:

- VAH או swing high.

ניקוד:

- Breakout volume confirmation: +15
- Market structure uptrend: +12
- Price above EMA20 / MA200 תורמים.

Entry Confirmation:

- close מעל trigger.
- retest held.
- אין falling candle.

### VWAP Reclaim

המערכת מזהה חזרה מעל VWAP אחרי שהמחיר היה מתחתיו.

תנאים:

- היה Close מתחת VWAP באחד הנרות האחרונים.
- המחיר הנוכחי קרוב ל-VWAP:

```text
abs(current_price - vwap) <= 0.3 ATR
```

- Volume לא יורד בצורה בעייתית.
- Anchored VWAP context לא שלילי.

Stop:

- מתחת ל-VWAP/רמת תמיכה, עם התאמת ATR.
- אם יש Fib, Stop יכול להיצמד נמוך יותר לפי Fib 78.6.

Target 1:

```text
vwap + 0.5 ATR
```

Target 2:

- VAH.
- לאחר מכן target normalization יכול להרחיב/לתקן.

Entry Confirmation:

- close מעל VWAP proxy.
- close >= open.
- close >= previous close.
- hold/follow-through.

### Volume-Price Scenario

המערכת מסווגת את התנהגות המחיר והנפח בארבעת המצבים הקלאסיים.

החישוב:

- משווה את 5 הנרות האחרונים מול 5 הנרות שלפניהם.
- בודקת האם המחיר עלה או ירד.
- בודקת האם הנפח הממוצע עלה או ירד.

סיווגים:

- price_up_vol_up
- price_up_vol_down
- price_down_vol_down
- price_down_vol_up

ניקוד:

- price_up_vol_up: +8
- price_down_vol_down: +8
- price_down_vol_up: -10

היגיון:

- עלייה במחיר עם עלייה בנפח יכולה להראות ביקוש.
- ירידה במחיר עם ירידה בנפח יכולה להראות pullback בריא.
- ירידה במחיר עם עלייה בנפח יכולה להראות מכירה מוסדית ולכן מקבלת עונש.

### Relative Strength

יש שתי שכבות Relative Strength:

1. בסורק הטכני:
   - משווה תשואה של המניה מול SPY ב-20 ימים.
   - יחס מעל 1.0 אומר שהמניה חזקה מ-SPY.

2. ב-Smart Universe:
   - משווה תשואה של 3 חודשים מול SPY ו-QQQ.
   - משקל: 65% SPY ו-35% QQQ.

ניקוד Setup:

- Relative Strength > 1.3: +10
- Relative Strength < 0.7: -8

היגיון:

- Long setups במניות שמובילות את השוק עדיפים על מניות מפגרות.

### Professional Market Regime

בנוסף ל-Agent Market Regime, יש שכבת Professional Context בתוך תוצאת הסריקה.

היא בודקת:

- SPY trend.
- QQQ trend.
- IWM trend.

סיווג:

- Risk-on
- Mixed
- Risk-off

השפעה:

- לא מחליפה את Agent Market Regime.
- משפיעה על quality score שמוצג למשתמש.
- מוסיפה warnings/strengths.

### Liquidity Quality

המערכת בודקת נזילות גם אחרי הסינון הראשוני.

המדדים:

- מחיר נוכחי.
- Average Volume ל-20 ימים.
- Average Dollar Volume ל-20 ימים.

סיווג:

- Institutional liquidity
- Tradable
- Thin

ב-Professional Context:

- מחיר מעל 10 דולר מקבל חלק מהניקוד.
- Dollar volume מעל 20 מיליון דולר ו-volume מעל 300,000 מקבלים ניקוד נזילות.

הערה:

- Smart Universe מחמיר יותר: הוא דורש 100 מיליון דולר average dollar volume.
- Professional Context משמש להסבר/ציון בתוך תוצאת הסריקה.

### Trend Quality

המערכת בודקת איכות מגמה:

- מחיר מעל MA20.
- מחיר מעל MA50.
- מחיר מעל MA200.
- MA20 מעל MA50.
- MA50 מעל MA200.
- slope חיובי של MA20.

ניקוד Professional Context:

- מעל MA20: 0.18
- מעל MA50: 0.20
- מעל MA200: 0.20
- MA20 מעל MA50: 0.18
- MA50 מעל MA200: 0.14
- slope חיובי: 0.10

סיווג:

- Clean uptrend
- Constructive
- Messy

השפעה:

- Setup טכני עם Trend Quality חלש יקבל ציון איכות נמוך יותר.
- אם trend quality הוא Messy, המערכת מוסיפה warning.

### Volume Confirmation

המערכת בודקת האם הנפח תומך ב-Setup.

המדדים:

- יחס נפח הנר האחרון מול ממוצע 20 ימים.
- מספר ימי accumulation.
- מספר ימי distribution.
- האם pullback מתרחש בנפח יורד.

Accumulation day:

```text
return > 0.5% AND volume > avg20
```

Distribution day:

```text
return < -0.5% AND volume > avg20
```

ניקוד:

- volume ratio >= 1.1 מוסיף ניקוד.
- accumulation >= distribution מוסיף ניקוד.
- pullback volume contracting מוסיף ניקוד.

סיווג:

- Confirmed
- Neutral
- Distribution risk

### Event Risk

המערכת בודקת אירועים שעלולים לעוות Setup.

נבדק:

- Earnings date.
- האם הדוחות קרובים.
- gap גדול ב-20 ימים אחרונים.

סיווג:

- Earnings risk
- Recent gap risk
- No major event flag

השפעה:

- ב-Professional Context, דוחות קרובים מורידים quality.
- ב-Agent Risk Layer, Earnings Blackout יכול לחסום BUY לגמרי.

### Extended-Hours Analysis

המערכת מושכת quote ל-pre-market / after-hours כאשר זמין.

מה נשמר:

- phase.
- label.
- quote_price.
- timestamp.
- regular_close.
- change.
- change_pct.
- האם זה extended.

המערכת גם מחשבת impact:

- האם המחיר extended בתוך buy zone.
- האם המחיר extended מתחת buy zone.
- האם המחיר extended מעל buy zone.
- האם extended נגע ב-stop.
- האם extended נגע ב-target 1.
- האם extended נגע ב-target 2.
- R/R לפי המחיר extended לצורכי מידע.

חשוב:

```text
Extended-hours quote is informational only.
```

כלומר:

- הוא מוצג למשתמש.
- הוא יכול לסמן שה-setup השתנה.
- הוא לא פותח BUY_SIMULATED בלי confirmation בזמן regular session.

### Target Normalization

אחרי שה-Setup מציע targets, המערכת מתקנת אותם אם צריך.

מטרות:

- לוודא ש-Target 1 מעל entry.
- לוודא ש-Target 1 לא קרוב מדי.
- לוודא ש-Target 2 מעל Target 1.
- להשתמש במבנה שוק אם אפשר.

כללים:

```text
minimum_t1_atr = 1.2 ATR
fallback_t1 = entry + 2.0 ATR
fallback_t2 = entry + 4.0 ATR
```

אם Target 1 מתחת entry:

```text
target_1 = entry + 2 ATR
```

אם Target 1 קרוב מדי:

```text
target_1 = entry + 1.2 ATR
```

אם Target 2 לא מעל Target 1:

```text
target_2 = max(structure_target, entry + 4 ATR, target_1 + ATR)
```

### Nearest Structure Target

כאשר ניתן, יעד נבחר לפי מבנה ולא רק נוסחה מכנית.

החישוב:

- מסתכל על עד 63 ימים אחורה.
- מחפש highs קודמים מעל entry + 0.75 ATR.
- בוחר את ההתנגדות הקרובה ביותר מעל המחיר.

אם אין resistance מתאים:

- משתמש ב-fallback מכני לפי ATR / measured move.

### Trade Plan

לכל setup שאינו No Trade, המערכת בונה Trade Plan.

הוא כולל:

- entry_trigger.
- trigger_price.
- invalidation.
- stop_loss.
- target_1.
- target_2.

דוגמאות:

- Breakout: close above resistance / reclaim.
- VWAP: hold VWAP and break above trigger.
- Fib/support: break above prior-day high after holding buy zone.

Trade Plan הוא הסבר ותיעוד. הוא לא מחליף את Agent Risk Layer.

## סוגי Setups שהמערכת מזהה

### 1. No Trade

אם אין תבנית נקייה, המניה מקבלת:

```text
setup_type = No Trade
```

No Trade לא אומר שהחברה גרועה. זה אומר שאין כרגע מבנה טכני שמצדיק כניסה לפי החוקים.

### 2. Fib 61.8 Confluence Buy Zone

Setup שמחפש אזור תיקון סביב Fib 61.8 עם confluence.

נדרש:

- מחיר קרוב לאזור Fib רלוונטי.
- confluence עם POC / VAL / VWAP.
- Entry תיאורטי באזור ה-Fib.
- Stop מתחת Fib 78.6 עם מרווח ATR.
- Targets לפי VWAP / Fib / VAH / swing high.
- R/R מינימלי כמועמד.

ה-Agent לא יקנה רק כי המחיר באזור. הוא דורש candle confirmation:

- סגירה מעל buy zone, או
- reclaim שורי חזק מתוך האזור, או
- אין candle חלש שנופל לתוך האזור.

### 3. Breakout + Retest

Setup שמחפש פריצה מעל resistance ואז חזרה לבדיקה.

נדרש:

- Pivot high קודם.
- Close מעל רמת resistance אחרי אותו pivot.
- Retest zone סביב resistance.
- המחיר הנוכחי נמצא באזור retest.
- לא הייתה שבירה עמוקה מתחת ל-resistance פחות 1.5 ATR אחרי הפריצה.
- breakout volume נחשב כבונוס אם הנפח ביום הפריצה >= 1.5x ממוצע 20 ימים.

Entry:

- executable entry הוא המחיר הנוכחי.
- Stop: resistance - 1.5 ATR.
- Target 1: לפי resistance/measured move או resistance structure.
- Target 2: VAH או swing high.

### 4. Swing Low + Volume Support Buy Zone

Setup שמחפש אזור תמיכה סביב swing low משמעותי עם Volume Support.

נדרש:

- Swing low תקף.
- confluence עם Volume Profile.
- מחיר קרוב לאזור התמיכה.
- Stop מתחת לאזור התמיכה.
- Targets מעל המחיר.

### 5. Liquidity Trap Buy Zone

Setup שמחפש sweep / stop hunt / reclaim.

הרעיון:

- המחיר ירד מתחת לרמת תמיכה או VAL.
- לאחר מכן נסגר חזרה מעליה.
- זה יכול להראות ניקוי נזילות וחזרה פנימה.

זה Setup מסוכן יותר, לכן שכבת ה-Agent מחמירה:

- לא קונים ב-BEAR.
- לא קונים בסקטור WEAK.
- צריך confirmation.
- אם אין reclaim אמין, זה WATCH ולא BUY.

### 6. VWAP Reclaim Setup

Setup שמחפש reclaim מעל VWAP.

נדרש:

- מחיר חזר מעל VWAP proxy.
- נר הושלם מעל VWAP.
- רצוי follow-through.
- לא מספיק רק לגעת ב-VWAP.

## ניקוד ה-Setup הטכני

ה-setup מקבל score בין 0 ל-1.

הניקוד נבנה ממשקולות טכניות. המקסימום התיאורטי הוא 248 נקודות, ואז מנורמל ל-0 עד 1.

דוגמאות למשקולות:

- Sweep and reclaim: 30
- POC confluence: 25
- Fib 61.8 proximity: עד 20
- Fib zone swept and reclaimed: 20
- R/R quality: עד 20
- VAL reclaim after sweep: 15
- Volume confirmed VWAP reclaim: 12
- VAL confluence: 12
- Swing low proximity: 10
- Price above weekly MA200: 10
- Strong relative strength: 10
- HVN confluence: 8
- VWAP proximity: 8
- Price above EMA20: 8
- Breakout volume confirmation: 15

עונשים:

- מחיר מתחת EMA20: מינוס 15
- מחיר מתחת weekly MA200: מינוס 25
- Downtrend market structure: מינוס 20
- Price down + volume up scenario: מינוס 10
- Weak relative strength: מינוס 8
- מחיר בתוך Value Area: מינוס 20
- acceptance below swept level: מינוס 20

## R/R ברמת הסורק

הסורק לא מסתכל רק על target הרחוק ביותר.

יש שני targets:

- Target 1 - יעד ראשוני למימוש חלקי / הפחתת סיכון.
- Target 2 - יעד stretch להמשך מהלך.

בסורק הבסיסי:

```text
decision_rr = rr1 * 65% + rr2 * 35%
```

כדי שמניה תישאר מועמדת טכנית, היא צריכה לפחות:

- rr1 חיובי.
- max(rr1, rr2) >= min_rr.

הסיבה: הסורק אמור להציג מועמדים טכניים, גם אם אחר כך ה-Agent יחסום אותם בגלל R/R נטו, confirmation, חשיפה או סקטור.

## Market Regime

לפני כניסה, ה-Agent מחשב מצב שוק כללי:

- BULL
- NEUTRAL
- BEAR

הנתונים:

- SPY
- QQQ
- IWM
- VIX
- US10Y
- DXY

לוגיקת ניקוד:

- SPY bullish: +2
- SPY bearish: -2
- QQQ bullish: +2
- QQQ bearish: -2
- IWM לא bearish: +1
- IWM bearish: -1
- VIX calm או מתחת 20: +1
- VIX stressed או מעל 25: -2
- US10Y bullish: -0.5
- US10Y bearish: +0.25
- DXY bullish: -0.25
- DXY bearish: +0.25

סיווג:

- risk_points >= 4 -> BULL
- risk_points <= -2 -> BEAR
- אחרת -> NEUTRAL

השפעה:

```text
BULL:
  max total exposure = $40,000
  minimum net R/R = 2.0
  minimum setup score = 0.45

NEUTRAL:
  max total exposure = $20,000
  minimum net R/R = 2.5
  minimum setup score = 0.55

NEUTRAL + STRONG sector:
  minimum net R/R can be relaxed to 2.2

BEAR:
  no new BUY_SIMULATED
```

## חוקי כניסה ל-BUY_SIMULATED

המערכת תפתח BUY_SIMULATED רק אם כל השכבות עוברות.

חובה:

- setup_type אינו No Trade.
- Market Regime אינו BEAR.
- הסריקה היא בזמן regular session, אלא אם הוגדר אחרת.
- setup_score >= 0.45 בשוק BULL.
- setup_score >= 0.55 בשוק NEUTRAL.
- הסקטור אינו WEAK.
- normalized quality תקין.
- קיימת buy zone.
- קיים stop loss.
- קיימים target 1 ו-target 2.
- המחיר נמצא בתוך buy zone.
- executable entry תקין.
- net_rr_1 >= 0.80.
- target 2 לא יכול להצדיק כניסה לבד.
- weighted net R/R עובר את סף השוק.
- Target 1 לא קרוב מדי ביחס ל-ATR.
- entry confirmation עבר.
- אין earnings blackout.
- אין cooldown פעיל אחרי stop loss, אלא אם קיימת החרגה חזקה.
- אין חריגה מחשיפת סקטור.
- אין חריגה מחשיפת factor/theme.
- אין קורלציה גבוהה מדי עם פוזיציה קיימת.
- position size נכנס תחת מגבלות מזומן, חשיפה וסיכון.

אם אחד התנאים נכשל:

- לפעמים הפעולה תהיה WATCH.
- אם זה קרוב לכניסה או off-hours, הפעולה יכולה להיות WATCH_READY.
- אם יש חסימה מהותית, הפעולה תהיה SKIP.

## Entry Confirmation

ה-Agent לא קונה רק כי המחיר בתוך buy zone.

הוא משתמש בנר שהושלם, לא בנר חי.

ברירת מחדל:

```text
MARKET_LENS_REQUIRE_ENTRY_CONFIRMATION=true
MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_ENABLED=true
MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_INTERVAL=30m
MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_PERIOD=5d
```

Breakout + Retest:

- close מעל trigger.
- retest held.
- אין falling candle לתוך האזור.

VWAP Reclaim:

- close מעל VWAP proxy.
- close >= open.
- close >= previous close.
- hold מעל buy zone.

Fib/support:

- הנר נגע באזור.
- close מעל buy zone, או reclaim שורי חזק.
- reclaim שורי חזק דורש close מעל midpoint של ה-zone, close מעל open, close מעל previous close, וסגירה בחלק העליון של הנר.
- candle חלש או falling candle חוסם BUY.

אם אין מספיק נתונים לחישוב confirmation:

```text
BUY נחסם
הפעולה הופכת ל-WATCH
```

## Gross R/R מול Net R/R

המערכת שומרת גם R/R תיאורטי וגם R/R נטו.

Gross R/R:

- מחושב לפי entry, stop, target 1, target 2.
- לא כולל spread/slippage.

Net R/R:

- משתמש ב-executable entry.
- מוסיף spread.
- מוסיף slippage.
- מוסיף fees אם הוגדרו.
- מוריד slippage מה-target assumptions.
- מגדיל את הסיכון האפקטיבי.

משקולות Agent:

```text
weighted_net_rr = net_rr_1 * 80% + net_rr_2 * 20%
```

ספי ברירת מחדל:

```text
MARKET_LENS_MIN_PRIMARY_NET_RR=0.80
MARKET_LENS_PREFERRED_PRIMARY_NET_RR=1.00
MARKET_LENS_PRIMARY_RR_WEIGHT=0.80
MARKET_LENS_STRETCH_RR_WEIGHT=0.20
```

לוגיקת Slippage:

```text
avg dollar volume >= $500M:
  spread fallback = 0.03%
  slippage = 0.07%

avg dollar volume >= $100M:
  spread fallback = 0.08%
  slippage = 0.15%

avg dollar volume >= $25M:
  spread fallback = 0.15%
  slippage = 0.30%

below $25M:
  spread fallback = 0.25%
  slippage = 0.60%
```

Breakout מקבל תוספת slippage:

- +0.10% במניות נזילות.
- +0.20% במניות פחות נזילות.

אם ATR% גבוה מ-6%, slippage מוכפל ב-1.15.

התקרה ל-slippage היא 0.75%.

## Target Validation

המערכת בודקת אם היעדים סבירים ביחס ל-ATR ולמבנה השוק.

נשמרים:

- target_1_atr_distance
- target_2_atr_distance
- target_feasibility_status
- market_structure_status
- prior_high_20
- prior_high_63
- previous_resistance

סטטוסים:

- OK - היעדים סבירים.
- LOW_REWARD_DISTANCE - Target 1 קרוב מדי.
- AGGRESSIVE - היעד רחוק יחסית אבל לא פסול.
- EXTENDED - יעד רחוק מדי ביחס ל-ATR או מבנה.
- INVALID - יעד לא מעל מחיר.
- UNKNOWN - אין מספיק נתונים.

ברירת מחדל:

```text
MARKET_LENS_MIN_TARGET1_ATR_DISTANCE=0.75
```

כללים:

- Target 1 חייב להיות מעל המחיר.
- Target 2 חייב להיות מעל המחיר.
- Target 1 קרוב מדי ל-ATR גורם ל-WATCH_READY/WATCH.
- Target 1 מעל 7 ATR או Target 2 מעל 14 ATR מסומנים כ-EXTENDED.
- אם היעד מעל מבנה התנגדות אחרון בצורה מוגזמת, הוא מסומן כ-EXTENDED.

## Earnings Blackout

ה-Agent בודק תאריך דוחות.

כללים:

- אין BUY_SIMULATED חדש 5 ימי מסחר לפני דוח.
- אין BUY_SIMULATED חדש יום מסחר אחד אחרי דוח.
- אם אין נתוני דוחות, ברירת המחדל היא לא לחסום, אבל להוסיף warning.

ניתן לשנות:

```text
MARKET_LENS_BLOCK_UNKNOWN_EARNINGS=false
```

## Cooldown אחרי Stop Loss

אם מניה יצאה ב-EXIT_STOP, יש cooldown לפני כניסה חדשה.

ברירת מחדל:

```text
MARKET_LENS_STOP_COOLDOWN_DAYS=3
```

כניסה חדשה בזמן cooldown תתאפשר רק אם:

- setup_score >= 0.60
- net_rr_1 >= 1.20
- entry_confirmation_passed=true
- setup_type החדש שונה מה-setup שנכשל

אחרת:

```text
final_action = WATCH
```

## Correlation Check

לפני BUY, המערכת בודקת קורלציה מול פוזיציות פתוחות.

החישוב:

- Daily returns.
- עד 90 ימים אחרונים.
- דרוש לפחות 40 תצפיות חופפות.
- נבדקת הקורלציה מול כל פוזיציה פתוחה.

ברירת מחדל:

```text
MARKET_LENS_CORRELATION_BLOCK_THRESHOLD=0.85
```

אם יש קורלציה גבוהה:

```text
WATCH: High correlation with existing position
```

המטרה היא למנוע תיק שנראה מפוזר לפי טיקרים אבל בפועל חשוף לאותו factor.

## Factor / Theme Tags

לכל מניה מתווספים tags לפי סקטור ותמה.

דוגמאות:

- Mega Cap Tech
- AI / Semiconductors
- High Beta Growth
- Defensive
- Financials
- Energy
- Consumer Cyclical
- Low Volatility
- Small Cap / Risk-On
- Rates-sensitive Growth

המערכת בודקת חשיפה גם לפי factor/theme, לא רק לפי סקטור.

ברירת מחדל:

```text
BULL factor exposure cap = 50% of max market exposure
NEUTRAL factor exposure cap = 35% of max market exposure
```

## ניהול חשיפה וסיכון בתיק

ה-Agent מנהל תיק סימולטיבי של:

```text
$100,000
```

חוקי ברירת מחדל:

- Max allocation per ticker: 10% מהתיק, עד 10,000 דולר.
- Max risk per trade: 1% מהתיק, עד 1,000 דולר.
- BULL max total exposure: 40,000 דולר.
- NEUTRAL max total exposure: 20,000 דולר.
- BEAR: אין פתיחת עסקאות חדשות.
- BULL sector exposure cap: 40% מהחשיפה המותרת.
- NEUTRAL sector exposure cap: 30% מהחשיפה המותרת.
- BULL factor exposure cap: 50% מהחשיפה המותרת.
- NEUTRAL factor exposure cap: 35% מהחשיפה המותרת.

Position sizing מחושב לפי הקטן מבין:

- cash available.
- max position allocation.
- max risk לפי entry-stop.
- remaining market-regime exposure.
- sector exposure cap.
- factor exposure cap.

אם פוזיציה איכותית גדולה מדי:

- ה-Agent מנסה להקטין כמות מניות.
- אם הכמות המוקטנת עדיין לא עומדת בחוקים, הפעולה יורדת ל-WATCH.

## Off-hours Scans

המערכת יכולה לסרוק מחוץ לשעות המסחר.

מטרת off-hours:

- לזהות מועמדים.
- לשמור WATCH_READY.
- להתכונן לסריקת אישור בתחילת המסחר.

אבל:

```text
MARKET_LENS_ALLOW_BUY_OUTSIDE_REGULAR_HOURS=false
```

כלומר:

- אין BUY_SIMULATED מחוץ ל-Regular Session.
- pre-market/after-hours משמשים מידע בלבד.
- אם יש setup טוב מחוץ למסחר, הוא מחכה ל-confirmation בזמן מסחר.

זיהוי session:

- PRE_MARKET: 04:00-09:29 New York.
- REGULAR: 09:30-16:00 New York.
- AFTER_HOURS: 16:01-20:00 New York.
- WEEKEND/CLOSED: אין פתיחת עסקאות.

## Action Types

הפעולות האפשריות:

### BUY_SIMULATED

פתיחת פוזיציה סימולטיבית.

מתאפשר רק אם כל חוקי הכניסה, הסיכון והאישור עברו.

### WATCH_READY

Setup קרוב לכניסה או staged מחוץ לשעות המסחר.

לדוגמה:

- Setup תקין אבל מחוץ לשעות regular session.
- Net R/R קרוב לסף.
- Target 1 קרוב מדי אבל המבנה עדיין מעניין.

### WATCH

יש setup טכני, אבל חסרים תנאים לכניסה.

לדוגמה:

- מחיר לא בתוך buy zone.
- confirmation לא עבר.
- net R/R לא מספיק.
- סקטור לא מספיק חזק.
- חשיפה/קורלציה בעייתית.

### SKIP

אין setup, או שיש חסימה מהותית.

לדוגמה:

- No Trade.
- Earnings blackout.
- Target validation invalid.
- Bear market.
- נתונים חסרים קריטיים.

### HOLD

פוזיציה קיימת נשארת פתוחה.

### TAKE_PARTIAL_PROFIT

Target 1 נגע.

המערכת מוכרת חצי מהכמות הסימולטיבית ומקדמת stop ל-entry.

### TAKE_PROFIT

Target 2 נגע.

המערכת סוגרת את יתרת הפוזיציה.

### EXIT_STOP

Stop Loss נגע.

המערכת סוגרת את הפוזיציה ומעדכנת cooldown.

## ניהול פוזיציה אחרי כניסה

ה-Position Monitor הוא הרכיב שאחראי על פוזיציות פתוחות.

הוא לא פותח עסקאות חדשות.

הוא עושה:

- קורא פוזיציות פתוחות מה-Excel.
- מוריד 1-minute intraday candles.
- בודק High/Low של כל candle.
- מזהה נגיעה ב-TP1, TP2 או SL.
- מעדכן Excel.
- מעדכן dashboard.
- כותב event log.

סדר בדיקה שמרני:

1. Stop Loss.
2. Target 2.
3. Target 1.

אם באותו candle נגעו גם target וגם stop:

```text
המערכת בוחרת stop-first
```

הסיבה: אין מידע על הסדר האמיתי בתוך candle של דקה.

## חוקי TP1 / TP2 / SL

### Target 1

כאשר high של candle נוגע ב-Target 1:

- פעולה: TAKE_PARTIAL_PROFIT.
- נסגרת חצי מהכמות.
- יתרת הפוזיציה נשארת פתוחה.
- stop_loss מתעדכן ל-entry_price.
- realized P/L נרשם.
- notes מתעדכן: Partial profit taken; stop moved to breakeven.

### Target 2

כאשר high של candle נוגע ב-Target 2:

- פעולה: TAKE_PROFIT.
- כל יתרת הפוזיציה נסגרת.
- realized P/L נרשם.

### Stop Loss

כאשר low של candle נוגע ב-stop:

- פעולה: EXIT_STOP.
- כל יתרת הפוזיציה נסגרת.
- realized P/L נרשם.
- cooldown מתחיל.

## מה נשמר בכל החלטה

כל ticker שנסרק מקבל Decision JSON.

השדות המרכזיים:

- timestamp
- ticker
- company_name
- price
- market_regime
- market_regime_score
- sector
- sector_etf
- sector_regime
- sector_score
- market_cap
- market_cap_bucket
- setup_type
- setup_score
- buy_zone_low
- buy_zone_high
- stop_loss
- target_1
- target_2
- gross_rr_1
- gross_rr_2
- gross_rr_decision
- net_rr_1
- net_rr_2
- net_rr
- estimated_spread
- estimated_slippage
- executable_entry
- theoretical_entry
- earnings_date
- earnings_blackout
- sector_exposure_before
- sector_exposure_after
- factor_tags
- factor_exposure_before
- factor_exposure_after
- highest_correlation_ticker
- highest_correlation_value
- position_size
- cash_available
- portfolio_exposure_before
- portfolio_exposure_after
- final_action
- reason
- warnings

המטרה היא שכל החלטה תהיה ניתנת לבדיקה בדיעבד.

## איפה הנתונים נשמרים

ה-Agent שומר:

```text
agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx
agent_results/decisions/*.jsonl
agent_results/summaries/*.md
agent_results/screenshots/*.png
agent_results/charts/
agent_results/position_monitor/*.md
```

ה-dashboard בנתיב:

```text
/agent
```

קורא את הנתונים האלה ומציג:

- מצב תיק.
- פוזיציות פתוחות.
- פעולות אחרונות.
- Trade log.
- סיכום ריצה אחרונה.
- גרפים שמורים.
- checklist של תנאי כניסה.
- P/L.
- score calibration.

## מדיניות שמירת גרפים

כדי לא לנפח את ה-repo:

שומרים גרפים עבור:

- BUY_SIMULATED.
- HOLD / Open Positions.
- WATCH_READY.
- עד 5 rejected candidates קרובים לכניסה.

לא שומרים גרפים עבור:

- כל No Trade.
- כל מניה שנפסלה מוקדם.
- כל rejected candidate רגיל.
- setup score נמוך מאוד.

ברירת מחדל:

```text
MARKET_LENS_SAVE_REJECTED_CHARTS=false
MARKET_LENS_REJECTED_CHART_LIMIT=5
MARKET_LENS_REJECTED_CHART_MIN_SCORE=0.40
```

## שעות סריקה וניטור

הסריקה מופעלת דרך `/agent/trigger-scan`.

Regular-session confirmation scans:

- 09:45 New York
- 10:30 New York
- 11:30 New York
- 13:30 New York
- 14:30 New York
- 15:30 New York
- 16:15 New York

Off-hours staging scans:

- Weekdays: 06:30, 08:30, 09:10, 16:20, 18:30, 20:15, 22:30 New York.
- Saturday: 11:00 New York.
- Sunday: 18:30, 22:00 New York.

Position live monitor:

- מופעל דרך `/agent/monitor-live`.
- מומלץ להריץ כל דקה בימים שני-שישי.
- שעות מומלצות: 09:35-16:05 New York.
- ה-monitor-live רק מטריג את GitHub Action אם יש פוזיציה פתוחה ונגיעה ב-TP/SL.

## איך לקרוא את התוצאה

דוגמה ל-BUY_SIMULATED:

```text
BUY_SIMULATED:
Bull/Neutral market acceptable,
sector not weak,
valid setup,
entry confirmation passed,
net R/R passed,
no earnings blackout,
correlation acceptable,
position size fits risk limits.
```

דוגמה ל-WATCH:

```text
WATCH:
Technical setup exists, but entry confirmation failed,
or net R/R is below threshold,
or price is not executable inside buy zone.
```

דוגמה ל-WATCH_READY:

```text
WATCH_READY:
Setup is close to entry or staged outside regular hours.
Needs regular-session confirmation before BUY_SIMULATED.
```

דוגמה ל-SKIP:

```text
SKIP:
No Trade, earnings blackout, invalid targets,
bear market block, or major risk/data issue.
```

## מה המערכת לא עושה

המערכת לא:

- נותנת ייעוץ פיננסי.
- מבטיחה הצלחה.
- מחברת לברוקר.
- פותחת עסקאות אמיתיות.
- משתמשת בכסף אמיתי.
- קונה רק בגלל שהמחיר בתוך buy zone.
- משתמשת ב-Target 2 רחוק כדי להצדיק לבד עסקה חלשה.
- פותחת קנייה מחוץ לשעות regular session כברירת מחדל.

## נקודות חשובות לשיפור עתידי

כיווני שיפור אפשריים:

- Backtest מסודר לפי setup type.
- Walk-forward validation.
- השוואת performance לפי setup_score buckets.
- בדיקה האם setup_score גבוה באמת מנבא win rate גבוה יותר.
- שיפור Anchored VWAP לפי swing high/low.
- כיול slippage לפי נתוני bid/ask אמיתיים כאשר זמינים.
- ניתוח סקטורים לפי breadth פנימי ולא רק ETF.
- הגדלת universe ל-200/300 דרך batching, אם 100 לא מספיק.

## סיכום

Market Lens Agent הוא לא רק Scanner. הוא מערכת החלטה רב-שכבתית:

```text
Universe
-> Filters
-> Sector Health
-> Stock Score
-> Technical Setup
-> Professional Context
-> Market Regime
-> Entry Confirmation
-> Net R/R
-> Earnings / Exposure / Correlation
-> Position Sizing
-> Final Action
-> Portfolio Monitor
```

העיקרון המרכזי:

```text
לא כל מניה טובה היא טרייד.
לא כל Setup הוא כניסה.
כניסה מתבצעת רק כאשר הטכני, השוק, הסקטור, הסיכון והביצוע כולם מסתדרים יחד.
```
