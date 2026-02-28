from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Listing(Base):
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    photo_urls = Column(JSON, default=list)
    current_bid = Column(Float, default=0.0)
    auction_end_time = Column(DateTime, nullable=True)
    maxsold_url = Column(String, unique=True, nullable=False)
    category = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    notified = Column(Boolean, default=False)

    price_research = relationship("PriceResearch", back_populates="listing", cascade="all, delete-orphan")
    bid_history = relationship("BidHistory", back_populates="listing", cascade="all, delete-orphan")


class PriceResearch(Base):
    __tablename__ = "price_research"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    estimated_value = Column(Float, nullable=True)
    max_bid_price = Column(Float, nullable=True)
    fb_marketplace_ceiling = Column(Float, nullable=True)
    condition_score = Column(String, default="")
    condition_notes = Column(Text, default="")
    deal_flag = Column(Boolean, default=False)
    ebay_comps_raw = Column(JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    listing = relationship("Listing", back_populates="price_research")


class BidHistory(Base):
    __tablename__ = "bid_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    bid_amount = Column(Float, nullable=False)
    recorded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    listing = relationship("Listing", back_populates="bid_history")


class Watchlist(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_model = Column(String, nullable=False, unique=True)
    keywords = Column(JSON, default=list)
