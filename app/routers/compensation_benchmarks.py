from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import CompensationBenchmark, get_db, User
from app.auth import require_roles

router = APIRouter(prefix="/compensation-benchmarks", tags=["compensation-benchmarks"])

# Section 10 explicitly lists "Internal Compensation Benchmarking" as
# restricted to recruitment + leadership — same tier as compensation_range
# itself, not a separate, looser access level.
RESTRICTED_TO = ("leadership", "recruitment")


class BenchmarkCreate(BaseModel):
    role_category: str
    experience_range: Optional[str] = None
    typical_market_band_min: Optional[float] = None
    typical_market_band_max: Optional[float] = None
    currency: Optional[str] = "INR"


class BenchmarkUpdate(BaseModel):
    experience_range: Optional[str] = None
    typical_market_band_min: Optional[float] = None
    typical_market_band_max: Optional[float] = None
    currency: Optional[str] = None


@router.post("", status_code=201)
def create_benchmark(
    payload: BenchmarkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*RESTRICTED_TO)),
):
    benchmark = CompensationBenchmark(**payload.dict(), last_updated_by=user.email)
    db.add(benchmark)
    db.commit()
    db.refresh(benchmark)
    return {"id": benchmark.id, "role_category": benchmark.role_category}


@router.get("")
def list_benchmarks(db: Session = Depends(get_db), user: User = Depends(require_roles(*RESTRICTED_TO))):
    benchmarks = db.query(CompensationBenchmark).all()
    return [
        {"id": b.id, "role_category": b.role_category, "experience_range": b.experience_range,
         "typical_market_band_min": b.typical_market_band_min, "typical_market_band_max": b.typical_market_band_max,
         "currency": b.currency, "last_updated_by": b.last_updated_by, "last_updated_at": b.last_updated_at}
        for b in benchmarks
    ]


@router.patch("/{benchmark_id}")
def update_benchmark(
    benchmark_id: int,
    payload: BenchmarkUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*RESTRICTED_TO)),
):
    """Section 11: 'The recruitment team updates this repository manually
    over time' — no AI agent writes to this table."""
    benchmark = db.query(CompensationBenchmark).get(benchmark_id)
    if not benchmark:
        raise HTTPException(404, "Benchmark not found")
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(benchmark, field, value)
    benchmark.last_updated_by = user.email
    from datetime import datetime
    benchmark.last_updated_at = datetime.utcnow()
    db.commit()
    return {"id": benchmark.id, "updated": True}
