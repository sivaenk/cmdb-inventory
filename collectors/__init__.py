"""
AWS Resource Collectors — shared library for multi-account resource collection.

This package provides:
- collectors: Resource-specific AWS API collectors
- models: ResourceNode dataclass
- utils: AWS session helpers, structured logging
- enrichers: Relationship discovery between resources
- validation: Post-collection data quality checks
"""

__version__ = "1.0.0"

from aws_resource_collectors.models.resource_node import ResourceNode

__all__ = ["ResourceNode", "__version__"]
