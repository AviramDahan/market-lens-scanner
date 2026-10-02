# Qualified Selection and Sector Gate Audit

תאריך הבדיקה: 2026-10-02

תקופת החלטות: 2026-09-17 עד 2026-09-29

תצפית תוצאות עד: 2026-10-02

מצב: ניתוח קריאה בלבד. לא שונו אסטרטגיה פעילה, ספים, Gates, sizing, exits, תיק, התראות או Production.

## מסקנה קצרה

1. הדיווח על `qualified_selection_v1` נכון: שש עסקאות סגורות, כולן הפסדיות, עם PnL נטו מצטבר של `-$817.73` ועלויות מדומות של `$42.34`.
2. המדגם מרוכז מאוד: כל שש העסקאות הן `Fib 61.8 Confluence Buy Zone`, כולן ב-`NEUTRAL`, כולן בסקטור `STRONG`, וכולן נכנסו דרך `neutral_pilot`. אי אפשר להסיק ממנו שהמערכת כולה או כל סוגי ה-setup אינם תקינים.
3. לא נמצאה ראיה שסינון `WEAK sector` לבדו חסם עסקאות שהיו עוברות את כל יתר שערי הכניסה. מתוך 450 הזדמנויות ייחודיות בסקטור חלש, כולן נכשלו גם בתנאי פעיל נוסף. Gate שלא נבדק סווג `NOT_EVALUATED`, לא `PASS`.
4. ה-Shadow הקיים מצא 24 אותות שבהם שינוי `sector_regime` בלבד מ-`WEAK` ל-`NEUTRAL` הפך אסטרטגיית Shadow ל-`would_buy=true`. אלה אינם מועמדים שעברו את ה-Agent הפעיל: כל 24 נכשלו גם ב-Gates פעילים אחרים.
5. מתוך 17 אותות Shadow שכבר נסגרו בתצפית, 14 היו שליליים ורק 3 חיוביים. סכום התוצאות הסגורות היה `-12.40R`; שבעה אותות נוספים עדיין censored. זה לא מוכיח שהסקטור החלש גורם להפסד, אבל אינו תומך כרגע בהסרת הסינון.
6. לכן אין בסיס נתונים להחלשת Gate הסקטור או Gate אחר. הניסוי הבא צריך למדוד רק מועמדים שעוברים במפורש את כל התנאים האחרים ושנכשלים רק בסקטור.

## מקור האמת ומתודולוגיה

- עסקאות: `agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx`, מאוגדות למחזור חיים מלא באמצעות `read_trades` ו-`compute_full_trade_performance` ב-`app/agent_dashboard.py`.
- החלטות היסטוריות: ארכיון `agent_results/decisions` מה-commit `a9bdc4c30fe21365be8f2c2ad5de325b0435c21c`.
- Deduplication: הרשומה הראשונה ב-regular session לכל `New York date + ticker + setup`; אין בחירה בדיעבד של הרשומה הטובה באותו יום.
- השוואת סקטור: `evaluate_shadow_strategies` הופעל פעמיים על אותו record; רק `sector_regime` הוחלף מ-`WEAK` ל-`NEUTRAL`. וריאנטים שמשנים stop הוצאו מההשוואה.
- replay: נתוני OHLC של 5 דקות, regular session בלבד, החל אחרי חותמת זמן האות; entry/stop/targets ועלויות הוקפאו בזמן האות. אם stop ו-target נגעו באותו נר, stop קודם. ב-TP1 נמכר חצי וה-stop של היתרה עבר ל-entry; לאחר מכן TP2.
- כל אות נמדד בנפרד. התוצאות אינן תשואת תיק ואינן עצמאיות, מפני שחלקן חופפות בזמן או בטיקר.

## שש עסקאות qualified_selection_v1

| Trade | Entry / Exit | Entry / Stop / TP1 / TP2 | Score / Regimes | RR1 / RR2 / weighted | מהלך ותוצאה נטו |
|---|---|---|---|---|---|
| DDOG `20260917_150131-DDOG` | 2026-09-17 15:03 / 2026-09-18 14:38Z | 236.68 / 229.77 / 250.96 / 269.51 | 0.538; NEUTRAL; Technology STRONG | 1.99 / 4.60 / 2.51 | EXIT_STOP 6 @ 229.57; `-$42.66`, `-1.00R`; costs $2.41; MFE 0.34R, MAE 1.03R |
| APP `20260917_143144-APP` | 2026-09-17 14:34 / 2026-09-18 19:57Z | 317.83 / 308.49 / 336.66 / 452.23 | 0.461; NEUTRAL; Communication Services STRONG | 1.94 / 13.96 / 4.35 | EXIT_STOP 13 @ 308.22; `-$124.98`, `-1.00R`; costs $7.02; MFE 1.10R, MAE 1.07R |
| JNJ `20260921_193155-JNJ` | 2026-09-21 19:34 / 2026-09-22 13:36Z | 269.90 / 267.00 / 275.95 / 281.07 | 0.475; NEUTRAL; Healthcare STRONG | 1.89 / 3.52 / 2.22 | EXIT_STOP 18 @ 266.77; `-$56.39`, `-1.00R`; costs $8.25; MFE 0.07R, MAE 1.52R |
| ORCL `20260922_163150-ORCL` | 2026-09-22 16:33 / 2026-09-24 13:33Z | 148.92 / 143.83 / 157.44 / 170.70 | 0.486; NEUTRAL; Technology STRONG | 1.62 / 4.16 / 2.13 | gap מתחת ל-stop; EXIT_STOP 33 @ 137.19; `-$386.86`, `-2.25R`; costs $8.35; MFE 0.31R, MAE 2.46R |
| APP `20260923_193140-APP` | 2026-09-23 19:33 / 2026-09-29 13:42Z | 316.01 / 306.48 / 337.26 / 452.23 | 0.464; NEUTRAL; Communication Services STRONG | 2.15 / 13.89 / 4.50 | EXIT_STOP 15 @ 306.21; `-$146.92`, `-1.00R`; costs $8.05; MFE 1.27R, MAE 1.02R |
| JNJ `20260925_173145-JNJ` | 2026-09-25 17:33 / 2026-09-29 14:21Z | 270.10 / 267.00 / 276.06 / 281.07 | 0.539; NEUTRAL; Healthcare STRONG | 1.75 / 3.26 / 2.05 | EXIT_STOP 18 @ 266.77; `-$59.92`, `-1.00R`; costs $8.26; MFE 1.19R, MAE 1.04R |

### עובדות נתמכות

- כל lifecycle נספר פעם אחת; אין ספירה כפולה של partials. בפועל לא היו partial exits בשש העסקאות.
- העלויות הן כ-5.2% מההפסד הכולל ולכן אינן ההסבר המרכזי.
- ORCL לבדה מסבירה כ-47% מההפסד. מחיר הפתיחה/ה-reference היה 137.32 מול stop של 143.83, ולכן התוצאה של `-2.25R` היא gap risk, לא כפל עלויות או שגיאת חישוב.
- APP בשתי הכניסות ו-JNJ בכניסה השנייה עברו מעל `+1R MFE`, אך TP1 דרש 1.75R-2.15R. הן חזרו ל-stop בלי partial.
- DDOG, JNJ הראשונה ו-ORCL כמעט לא התקדמו לטובת העסקה לפני הכישלון.
- APP ו-JNJ נכנסו שוב לאותו סוג setup לאחר cooldown והפסידו פעמיים כל אחת.

### השערות, לא מסקנות

- ייתכן ש-TP1 רחוק מדי לחלק מ-Fib setups ב-NEUTRAL, משום ששלוש עסקאות עברו 1R וחזרו. שישה trades אינם מספיקים כדי לשנות exits.
- ייתכן ש-`neutral_pilot` בוחר תת-אוכלוסייה חלשה של Fib setups, אך אין כאן קבוצת ביקורת מספקת.
- ORCL ממחישה סיכון gap שלא ניתן למנוע באמצעות stop רגיל; היא אינה מוכיחה שה-entry היה שגוי.

## הזדמנויות WEAK sector

נמצאו 38,131 decision records בתקופה, ומתוכן 450 הזדמנויות ייחודיות לאחר dedupe.

במסלול Standard כל 450 סווגו `MULTI_FAIL`. הכשלים הנוספים השכיחים: setup score ב-448, weighted R/R ב-408, confirmation ב-386, confirmation freshness ב-199, primary RR ב-154, normalized quality ב-119, ו-position sizing בלתי אפשרי ב-103. ב-347 נוספים sizing כלל לא הוערך.

גם במסלול Neutral Pilot כל 450 היו `MULTI_FAIL`: setup score ב-411, confirmation ב-386, weighted R/R ב-325, confirmation freshness ב-199, primary RR ב-154, normalized quality ב-119, sizing בלתי אפשרי ב-103, ו-sizing לא מוערך ב-347.

הממצא המרכזי: בתקופה שנבדקה אין מועמד שאפשר לתאר ביושר כ-"עבר הכול ורק WEAK sector חסם אותו".

### 24 sector-only Shadow flips

העמודה `Other active failures` מציגה כשלים במסלול Neutral Pilot מלבד הסקטור. `NOT_EVALUATED` אינו מעבר sizing.

| Date | Ticker | Shadow strategy | Score | RR1 | wRR | Other active failures | Sizing | Outcome |
|---|---|---|---:|---:|---:|---|---|---|
| 2026-09-17 | AIG | TREND_PULLBACK_RECLAIM | 0.503 | 1.46 | 2.13 | pilot_daily_limit | FAIL | closed -1.00R |
| 2026-09-17 | KO | TREND_PULLBACK_RECLAIM | 0.405 | 0.83 | 1.09 | score, wRR, daily limit | NOT_EVALUATED | closed -1.00R |
| 2026-09-17 | URI | TREND_PULLBACK_RECLAIM | 0.406 | 1.72 | 3.04 | score, quality, daily limit | NOT_EVALUATED | closed +0.78R |
| 2026-09-17 | V | TREND_PULLBACK_RECLAIM | 0.471 | 0.91 | 1.15 | wRR, confirmation, daily limit | FAIL | closed -1.00R |
| 2026-09-18 | TJX | VWAP_RECLAIM | 0.382 | 0.87 | 2.91 | score, target quality | NOT_EVALUATED | censored +1.38R |
| 2026-09-21 | PH | VWAP_RECLAIM | 0.425 | 0.88 | 1.61 | score, wRR | NOT_EVALUATED | closed +0.42R |
| 2026-09-21 | HON | VWAP_RECLAIM | 0.310 | 0.88 | 2.34 | score, quality | NOT_EVALUATED | censored +1.13R |
| 2026-09-22 | PH | VWAP_RECLAIM | 0.443 | 0.88 | 1.60 | score, wRR | NOT_EVALUATED | censored +0.32R |
| 2026-09-23 | GE | VWAP_RECLAIM | 0.259 | 0.89 | 1.86 | score, quality, wRR | NOT_EVALUATED | closed -1.00R |
| 2026-09-23 | HON | VWAP_RECLAIM | 0.328 | 0.88 | 2.08 | score | NOT_EVALUATED | censored +0.24R |
| 2026-09-23 | LIN | VWAP_RECLAIM | 0.303 | 0.84 | 1.82 | score, quality, wRR | NOT_EVALUATED | closed +0.40R |
| 2026-09-24 | MCD | VWAP_RECLAIM | 0.279 | 0.86 | 1.97 | score, wRR | NOT_EVALUATED | closed -1.00R |
| 2026-09-24 | EQIX | VWAP_RECLAIM | 0.471 | 0.89 | 1.26 | wRR | NOT_EVALUATED | closed -1.00R |
| 2026-09-24 | KO | TREND_PULLBACK_RECLAIM | 0.453 | 1.39 | 1.55 | wRR, confirmation | NOT_EVALUATED | closed -1.00R |
| 2026-09-24 | AIG | TREND_PULLBACK_RECLAIM | 0.411 | 1.25 | 2.00 | score, wRR, confirmation | FAIL | closed -1.00R |
| 2026-09-24 | BAC | VWAP_RECLAIM | 0.413 | 0.86 | 1.88 | score, wRR | FAIL | closed -1.00R |
| 2026-09-24 | WFC | VWAP_RECLAIM | 0.363 | 0.89 | 1.34 | score, wRR | FAIL | closed -1.00R |
| 2026-09-25 | GE | VWAP_RECLAIM | 0.253 | 0.89 | 1.97 | score, quality, wRR | NOT_EVALUATED | closed -1.00R |
| 2026-09-25 | PEP | VWAP_RECLAIM | 0.391 | 0.84 | 1.88 | score, wRR | NOT_EVALUATED | closed -1.00R |
| 2026-09-25 | NEE | VWAP_RECLAIM | 0.373 | 0.84 | 2.58 | score, quality, target quality | NOT_EVALUATED | closed -1.00R |
| 2026-09-25 | HON | VWAP_RECLAIM | 0.330 | 0.87 | 2.17 | score | NOT_EVALUATED | censored +0.32R |
| 2026-09-29 | SO | VWAP_RECLAIM | 0.370 | 0.82 | 2.72 | score, quality, target quality | NOT_EVALUATED | censored +0.69R |
| 2026-09-29 | SBUX | VWAP_RECLAIM | 0.362 | 0.88 | 1.77 | score, wRR | NOT_EVALUATED | censored -0.32R |
| 2026-09-29 | NEE | VWAP_RECLAIM | 0.331 | 0.83 | 2.59 | score, quality, target quality | NOT_EVALUATED | closed -1.00R |

מתוך 17 closed signals: שלושה חיוביים, 14 שליליים, ממוצע `-0.73R`, median `-1.00R`. מבין שבעת ה-censored signals, marked R ממוצע `+0.54R`; אין לחבר אותו לתוצאה הסגורה או להציגו כתשואה.

## ניסוי Shadow יחיד להמשך

שם מוצע: `WEAK_SECTOR_OVERRIDE_V1`.

הניסוי ייצור signal רק כאשר:

1. זהו ה-record הראשון ב-regular session לאותו ticker/setup/date.
2. קיים setup פעיל ו-confirmation מושלם ורענן.
3. כל Gate שאינו sector הוא `PASS` מפורש, כולל score, normalized quality, RR1, weighted R/R, target quality, earnings, cooldown, correlation ו-sizing executable.
4. ה-Gate היחיד שנכשל הוא `sector_regime=WEAK`.
5. אם sizing או נתון אחר חסר, הרשומה מסומנת `UNASSESSABLE` ואינה signal.

יש להקפיא בזמן האות entry/stop/targets/cost policy, ולהמשיך עם אותו replay שמרני. המדדים שנקבעים מראש: מספר signals, closed/censored, expectancy ו-median ב-R, TP1 hit rate, stop rate, MFE/MAE, drawdown רציף של אותות, ופילוח לפי setup/regime/sector. לצורך השוואה יש להשתמש ב-STRONG-sector controls מאותו setup, regime ו-score bucket, בלי לחפש ספים בדיעבד.

אין לשקול שינוי פעיל לפני לפחות 50 signals, לפחות 30 closed outcomes, 20 ימי מסחר ושלושה סקטורים. גם תוצאה חיובית רק תצדיק review; היא לא תשנה Gate אוטומטית. expectancy לא חיובי או uncertainty רחבה משאירים את Gate הסקטור ללא שינוי.

## מגבלות

- `qualified_selection_v1` כולל רק שש עסקאות סגורות, כולן מאותו setup ובאותו regime.
- ל-347 מתוך 450 הזדמנויות weak-sector אין sizing evaluation היסטורי; הן אינן ראיה לעסקה executable.
- 5-minute OHLC אינו feed ארכיוני של broker ואינו מגלה סדר אירועים בתוך נר.
- שבעה אותות עדיין censored בתאריך החיתוך.
- אותות Shadow חופפים ואינם portfolio backtest.
- replay אינו כולל השפעת שוק, queue priority או bid/ask היסטורי אמיתי מעבר למודל העלויות שנשמר בהחלטה.

## שחזור

```powershell
$audit = Join-Path $env:TEMP "market-lens-sector-period-20260929"
New-Item -ItemType Directory -Force $audit | Out-Null
git archive --format=tar a9bdc4c30fe21365be8f2c2ad5de325b0435c21c agent_results/decisions |
  tar -xf - -C $audit

$env:PYTHONPATH = "."
python agent/qualified_sector_analysis.py `
  --decision-dir "$audit/agent_results/decisions" `
  --tracker agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx `
  --start 2026-09-17 `
  --end 2026-09-29 `
  --outcome-end 2026-10-02 `
  --output "$env:TEMP/market-lens-qualified-sector-analysis-20261002.json"

python -m pytest -q tests/test_decision_quality_analysis.py
```

מימוש כלי הניתוח נמצא ב-`app/decision_quality_analysis.py`; ה-CLI המשחזר נמצא ב-`agent/qualified_sector_analysis.py`; בדיקות ה-semantics נמצאות ב-`tests/test_decision_quality_analysis.py`.
