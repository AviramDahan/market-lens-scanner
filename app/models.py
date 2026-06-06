from pydantic import BaseModel, Field

ANALYSIS_PERIODS = ("3mo", "6mo", "1y", "2y")


class FibonacciInfo(BaseModel):
    swing_low: float
    swing_low_date: str       # YYYY-MM-DD
    swing_high: float
    swing_high_date: str      # YYYY-MM-DD
    fib_382: float
    fib_500: float
    fib_618: float
    fib_786: float
    zone: tuple[float, float]
    is_near: bool


class VolumeProfile(BaseModel):
    poc: float
    vah: float
    val: float
    hvn: list[float] = []  # high-volume nodes (bin midpoints with vol > 60% of POC)


class VolumeSupportedSwingLow(BaseModel):
    swing_low: float
    swing_low_date: str       # YYYY-MM-DD
    volume_level: float
    volume_type: str          # "POC" | "VAL" | "HVN"
    distance_atr: float       # abs(swing_low - volume_level) / ATR
    is_valid: bool
    price_near: bool          # current price within 0.3 ATR of swing_low
    sweep_detected: bool      # candle wicked below swing_low, closed back above
    accepted_below: bool      # price broke below and stayed below


class BreakoutRetestInfo(BaseModel):
    resistance_level: float
    resistance_date: str          # YYYY-MM-DD
    breakout_date: str            # YYYY-MM-DD
    breakout_volume_ratio: float  # breakout candle vol / 20-day avg vol
    is_holding: bool              # price >= resistance_level - 0.5 * ATR


class MarketRegimeInfo(BaseModel):
    label: str
    score: float = Field(ge=0.0, le=1.0)
    spy_trend: str
    qqq_trend: str
    iwm_trend: str
    risk_note: str


class RelativeStrengthInfo(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    vs_spy: float
    vs_qqq: float
    vs_iwm: float
    label: str


class LiquidityInfo(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    avg_volume_20d: float
    avg_dollar_volume_20d: float
    price_ok: bool
    volume_ok: bool
    label: str


class TrendQualityInfo(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    label: str
    above_ma20: bool
    above_ma50: bool
    above_ma200: bool
    ma20_above_ma50: bool
    ma50_above_ma200: bool
    slope_20d: float


class VolumeConfirmationInfo(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    label: str
    recent_volume_ratio: float
    pullback_volume_contracting: bool
    accumulation_days_20d: int
    distribution_days_20d: int


class EventRiskInfo(BaseModel):
    label: str
    earnings_date: str | None = None
    days_to_earnings: int | None = None
    has_near_earnings: bool = False
    recent_gap_pct: float = 0.0


class TradePlanInfo(BaseModel):
    entry_trigger: str
    trigger_price: float
    invalidation: str
    stop_loss: float
    target_1: float
    target_2: float
    risk_per_share: float
    shares_for_1000_risk: int


class ProfessionalAssessment(BaseModel):
    quality_score: float = Field(ge=0.0, le=1.0)
    grade: str
    decision: str
    warnings: list[str] = []
    strengths: list[str] = []


class ScanResult(BaseModel):
    ticker: str
    setup_type: str
    score: float = Field(ge=0.0, le=1.0)
    current_price: float
    buy_zone: tuple[float, float]
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward: float
    reason: str
    fibonacci: FibonacciInfo | None = None
    volume_supported_swing_low: VolumeSupportedSwingLow | None = None
    breakout_retest: BreakoutRetestInfo | None = None
    market_regime: MarketRegimeInfo | None = None
    relative_strength_info: RelativeStrengthInfo | None = None
    liquidity: LiquidityInfo | None = None
    trend_quality: TrendQualityInfo | None = None
    volume_confirmation: VolumeConfirmationInfo | None = None
    event_risk: EventRiskInfo | None = None
    trade_plan: TradePlanInfo | None = None
    professional_assessment: ProfessionalAssessment | None = None


class ScanRequest(BaseModel):
    tickers: list[str] = Field(min_length=1)
    min_rr: float = Field(default=2.0, gt=0)
    analysis_period: str = Field(default="6mo", pattern="^(3mo|6mo|1y|2y)$")
    user_label: str | None = Field(default=None, max_length=80)
    session_id: str | None = Field(default=None, max_length=80)


class ScanResponse(BaseModel):
    results: list[ScanResult]
    errors: dict[str, str] = {}


class SaveSetupRequest(BaseModel):
    result: ScanResult
    analysis_period: str = Field(default="6mo", pattern="^(3mo|6mo|1y|2y)$")
    chart_url: str | None = None
    user_label: str | None = Field(default=None, max_length=80)
    session_id: str | None = Field(default=None, max_length=80)
