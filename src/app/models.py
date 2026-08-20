from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from typing import Optional, Any, Generic, TypeVar, Union, Dict, List, Literal
from enum import Enum
from datetime import datetime, timezone

EXECUTION_AGENT_TYPE = "execution-agent"
EXECUTION_AGENT_VERSION = "1.0.0"
EXECUTION_SERVICE_VERSION = "1.3.1"
SCHEMA_VERSION = "1.0"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"

    @classmethod
    def _missing_(cls, value):
        """Accept broker/database TIF values without weakening the enum contract."""
        normalized = str(value or "").strip().upper()
        return cls.__members__.get(normalized)


class OrderStatus(str, Enum):
    PENDING = "pending"
    PLACED = "placed"
    PARTIALLY_FILLED = "partially_filled"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RiskApprovalStatus(str, Enum):
    APPROVED = "approved"
    USED = "used"
    REVOKED = "revoked"
    EXPIRED = "expired"


StrategyBucket = Literal[
    "core_dividend",
    "quality_growth",
    "value_rebound",
    "news_momentum",
    "unassigned",
]
TradePlanStatus = Literal[
    "draft",
    "risk_pending",
    "risk_approved",
    "manual_approval_required",
    "execution_ready",
    "rejected",
]
TradePlanSource = Literal[
    "single_analysis",
    "multi_analysis",
    "scanner",
    "manual",
    "replay",
]


class CreateOrderRequest(BaseModel):
    trade_id: Union[int, str] = Field(..., description="Globally unique trade ID")
    account_id: Union[int, str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[float] = None
    quantity: int
    time_in_force: TimeInForce = TimeInForce.GTC
    strategy_bucket: StrategyBucket = "unassigned"
    risk_approval_id: str
    final_quantity: int = Field(gt=0)
    guard_plan: Optional[Dict[str, Any]] = None
    protective_exit: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_limit_price(self) -> "CreateOrderRequest":
        if self.order_type == OrderType.LIMIT and self.price is None:
            raise ValueError("Price is required for limit orders")
        return self

    @model_validator(mode="after")
    def validate_risk_gate(self) -> "CreateOrderRequest":
        if not str(self.risk_approval_id).strip():
            raise ValueError("risk_approval_id is required")
        if self.final_quantity <= 0:
            raise ValueError("final_quantity must be greater than zero")
        if self.quantity != self.final_quantity:
            raise ValueError(
                "quantity must match final_quantity approved by Risk_Agent"
            )
        if not self.guard_plan and not self.protective_exit:
            raise ValueError("guard_plan or protective_exit is required")
        return self

    @model_validator(mode="after")
    def validate_not_shadow_order(self) -> "CreateOrderRequest":
        execution_mode = str(self.metadata.get("execution_mode") or "").strip().lower()
        lane = str(self.metadata.get("lane") or "").strip().lower()
        if execution_mode == "shadow" or lane == "shadow":
            raise ValueError("shadow_lane_cannot_execute_broker_orders")
        return self


class TradePlanRiskEnvelope(BaseModel):
    account_equity: Optional[float] = Field(default=None, gt=0)
    cash_available: Optional[float] = Field(default=None, ge=0)
    max_loss_amount: float = Field(..., gt=0)
    max_loss_pct: float = Field(..., gt=0, le=1)
    risk_per_share: Optional[float] = Field(default=None, gt=0)
    position_value: Optional[float] = Field(default=None, ge=0)
    position_pct: Optional[float] = Field(default=None, ge=0, le=1)
    reward_risk_ratio: Optional[float] = Field(default=None, gt=0)
    session_risk_loaded: bool = False
    portfolio_context_loaded: bool = False


class TradePlanExitEnvelope(BaseModel):
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    trailing_stop_pct: Optional[float] = Field(default=None, gt=0, lt=1)
    break_even_trigger_r: Optional[float] = Field(default=None, gt=0)
    partial_exit_pct: Optional[float] = Field(default=None, gt=0, lt=1)
    time_stop_minutes: Optional[int] = Field(default=None, gt=0)
    exit_reason: Optional[str] = None


class TradePlanExecutionRequest(BaseModel):
    plan_id: str
    correlation_id: str
    source: TradePlanSource = "single_analysis"
    status: TradePlanStatus = "risk_approved"
    account_id: Union[int, str]
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    entry_price: Optional[float] = Field(default=None, gt=0)
    limit_price: Optional[float] = Field(default=None, gt=0)
    quantity: int = Field(gt=0)
    final_quantity: Optional[int] = Field(default=None, gt=0)
    time_in_force: TimeInForce = TimeInForce.GTC
    strategy: str = "unassigned"
    strategy_bucket: StrategyBucket = "unassigned"
    final_verdict: str
    confidence_score: float = Field(ge=0, le=1)
    expected_r: Optional[float] = None
    risk: TradePlanRiskEnvelope
    exit: TradePlanExitEnvelope = Field(default_factory=TradePlanExitEnvelope)
    risk_approval_id: str
    manual_approval_required: bool = True
    dry_run: bool = False
    reasons: List[str] = Field(default_factory=list)
    guard_plan: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution_plan(self) -> "TradePlanExecutionRequest":
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required when order_type is limit")
        if not str(self.risk_approval_id).strip():
            raise ValueError("risk_approval_id is required before execution")
        reference_price = self.entry_price or self.limit_price
        if reference_price is not None and self.exit.stop_loss is not None:
            if self.side == OrderSide.BUY and self.exit.stop_loss >= reference_price:
                raise ValueError("buy trade stop_loss must be below entry/limit price")
            if self.side == OrderSide.SELL and self.exit.stop_loss <= reference_price:
                raise ValueError("sell trade stop_loss must be above entry/limit price")
        return self

    def to_order_request(self) -> CreateOrderRequest:
        protective_exit = self.exit.model_dump(mode="json")
        guard_plan = self.guard_plan or None
        return CreateOrderRequest(
            trade_id=self.plan_id,
            account_id=self.account_id,
            symbol=self.symbol.upper(),
            side=self.side,
            order_type=self.order_type,
            price=self.limit_price or self.entry_price,
            quantity=self.final_quantity or self.quantity,
            time_in_force=self.time_in_force,
            strategy_bucket=self.strategy_bucket,
            risk_approval_id=self.risk_approval_id,
            final_quantity=self.final_quantity or self.quantity,
            guard_plan=guard_plan,
            protective_exit=protective_exit,
            metadata=self.metadata,
        )


class TradeOrder(BaseModel):
    trade_id: Union[int, str]
    symbol: str
    quantity: int
    side: OrderSide
    order_type: OrderType = OrderType.MARKET


class PortfolioRiskApproval(BaseModel):
    symbol: str
    approved: bool = False
    status: Optional[str] = None
    strategy_bucket: StrategyBucket = "unassigned"
    risk_approval_id: Optional[str] = None
    approved_quantity: Optional[float] = None
    final_quantity: Optional[float] = None
    requested_quantity: Optional[float] = None
    approved_value: Optional[float] = None
    requested_value: Optional[float] = None
    target_weight: Optional[float] = None
    allocation_pct: Optional[float] = None
    target_value: Optional[float] = None
    risk_response: Dict[str, Any] = Field(default_factory=dict)
    guard_plan: Optional[Dict[str, Any]] = None
    protective_exit: Optional[Dict[str, Any]] = None
    execution: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PortfolioExecutionRequest(BaseModel):
    account_id: Union[int, str]
    approvals: List[PortfolioRiskApproval]
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.GTC
    price_by_symbol: Dict[str, float] = Field(default_factory=dict)
    default_price: Optional[float] = None
    side_by_symbol: Dict[str, OrderSide] = Field(default_factory=dict)
    default_side: OrderSide = OrderSide.BUY
    trade_id_prefix: str = "portfolio"


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order_id: int
    trade_id: Union[int, str]
    account_id: Union[int, str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[float] = None
    quantity: int
    time_in_force: TimeInForce
    strategy_bucket: StrategyBucket = "unassigned"
    status: OrderStatus
    broker_order_id: Optional[str] = None
    reason: Optional[str] = None
    executed_quantity: int = 0
    avg_execution_price: Optional[float] = None
    executed_at: Optional[datetime] = None
    guard_plan: Optional[Dict[str, Any]] = None
    protective_exit: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CreateOrderResponse(OrderResponse):
    pass


class ExecutionResult(BaseModel):
    status: OrderStatus
    broker_order_id: Optional[str] = None
    symbol: str
    side: OrderSide
    quantity: int
    avg_execution_price: Optional[float] = None
    executed_at: Optional[datetime] = None
    reason: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    broker_connected: bool
    mode: str


class ErrorDetail(BaseModel):
    code: str
    message: str


T = TypeVar("T")


class StandardAgentResponse(BaseModel, Generic[T]):
    status: str
    agent_type: str = EXECUTION_AGENT_TYPE
    version: str = EXECUTION_AGENT_VERSION
    schema_version: str = SCHEMA_VERSION
    correlation_id: Optional[str] = None
    data: Optional[T] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[dict] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence_score: Optional[float] = None

    @field_validator("schema_version")
    @classmethod
    def schema_version_must_be_semantic(cls, value: str) -> str:
        parts = value.split(".")
        if not all(part.isdigit() for part in parts):
            raise ValueError(
                'Schema version must be in semantic format (e.g., "1.0")'
            )
        return value


class Order(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order_id: int
    trade_id: Union[int, str]
    account_id: Union[int, str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[float] = None
    quantity: int
    time_in_force: TimeInForce
    strategy_bucket: StrategyBucket = "unassigned"
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: Optional[str] = None
    reason: Optional[str] = None
    executed_quantity: int = 0
    avg_execution_price: Optional[float] = None
    executed_at: Optional[datetime] = None
    guard_plan: Optional[Dict[str, Any]] = None
    protective_exit: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FillPayload(BaseModel):
    order_id: int
    trade_id: Union[int, str]
    symbol: str
    side: OrderSide
    quantity: int = Field(gt=0)
    fill_price: float = Field(gt=0)
    average_entry_price: Optional[float] = None
    fees: float = 0.0
    realized_pnl: Optional[float] = None
    broker_fill_id: Optional[str] = None
    broker_order_id: Optional[str] = None
    liquidity: Optional[str] = None
    filled_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    job_id: Union[int, str]
    order_id: int
    trade_id: Union[int, str]
    status: ExecutionJobStatus = ExecutionJobStatus.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RiskApproval(BaseModel):
    approval_id: str
    account_id: Union[int, str]
    symbol: str
    side: OrderSide
    approved_quantity: int
    status: RiskApprovalStatus = RiskApprovalStatus.APPROVED
    expires_at: datetime
    order_id: Optional[int] = None


class ReconciliationItem(BaseModel):
    order_id: int
    broker_order_id: Optional[str] = None
    previous_status: Optional[OrderStatus] = None
    current_status: Optional[OrderStatus] = None
    action: str
    message: Optional[str] = None


class ReconciliationReport(BaseModel):
    checked: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    items: List[ReconciliationItem] = Field(default_factory=list)