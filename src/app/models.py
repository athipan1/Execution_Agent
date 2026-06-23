from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, Any, Generic, TypeVar, Union, Dict, List, Literal
from enum import Enum
from datetime import datetime, timezone

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"

class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"

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

StrategyBucket = Literal["core_dividend", "value_rebound", "news_momentum", "unassigned"]

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
            raise ValueError("quantity must match final_quantity approved by Risk_Agent")
        if not self.guard_plan and not self.protective_exit:
            raise ValueError("guard_plan or protective_exit is required")
        return self

class TradeOrder(BaseModel):
    trade_id: Union[int, str]
    symbol: str
    quantity: int
    side: OrderSide
    order_type: OrderType = OrderType.MARKET

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
    agent_type: str = "execution-agent"
    version: str = "1.0.0"
    data: Optional[T] = None
    error: Optional[dict] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence_score: Optional[float] = None

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
    used_at: Optional[datetime] = None
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
