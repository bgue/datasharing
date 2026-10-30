"""Canadian personal income tax by jurisdiction (federal + province). See README.md."""
from .api import TaxCaError, TaxResult, compute, marginal_rates
from .tables import JURISDICTIONS, TAX_YEARS

__all__ = ["compute", "marginal_rates", "TaxResult", "TaxCaError", "JURISDICTIONS", "TAX_YEARS"]
