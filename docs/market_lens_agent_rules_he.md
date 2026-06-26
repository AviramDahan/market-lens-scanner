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
