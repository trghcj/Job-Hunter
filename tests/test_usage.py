from jobhunter.usage import MemoryBudget
from tests.test_phase0 import offline_sql


def test_memory_budget_counts_down():
    budget = MemoryBudget(2)
    assert budget.try_spend("adzuna")
    assert budget.try_spend("adzuna")
    assert not budget.try_spend("adzuna")
    assert budget.used == 2


def test_migration_creates_api_usage():
    sql = offline_sql()
    assert "CREATE TABLE api_usage (" in sql
    assert "PRIMARY KEY (day, provider)" in sql
