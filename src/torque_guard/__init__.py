"""TorqueGuard: reproducible equipment-quality risk reasoning prototype."""

from .agent import DigitalEmployee
from .reasoning import ExternalModelConfig
from .risk import RiskAnalyzer
from .workflow import RiskCaseWorkflow, WorkflowAction

__all__ = [
    "DigitalEmployee",
    "ExternalModelConfig",
    "RiskAnalyzer",
    "RiskCaseWorkflow",
    "WorkflowAction",
]
__version__ = "0.1.0"
