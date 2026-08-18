"""
ResourceNode dataclass — the core data model for all collected resources.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ResourceNode:
    """Represents a single AWS resource record."""

    ResourceId: str
    ResourceType: str
    AccountId: str
    Region: str
    Metadata: dict[str, Any] = field(default_factory=dict)
    Relationships: list[dict[str, Any]] = field(default_factory=list)
    DiscoveredAt: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    LastSeenAt: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for DynamoDB / JSON."""
        return {
            "ResourceId": self.ResourceId,
            "ResourceType": self.ResourceType,
            "AccountId": self.AccountId,
            "Region": self.Region,
            "Metadata": self.Metadata,
            "Relationships": self.Relationships,
            "DiscoveredAt": self.DiscoveredAt,
            "LastSeenAt": self.LastSeenAt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResourceNode":
        """Deserialize from a plain dict (e.g. DynamoDB item)."""
        return cls(
            ResourceId=data["ResourceId"],
            ResourceType=data["ResourceType"],
            AccountId=data["AccountId"],
            Region=data["Region"],
            Metadata=data.get("Metadata", {}),
            Relationships=data.get("Relationships", []),
            DiscoveredAt=data.get("DiscoveredAt", ""),
            LastSeenAt=data.get("LastSeenAt", ""),
        )
