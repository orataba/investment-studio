import pytest

from portfolio_app.services.concentration import ConcentrationUnavailable, project_portfolio_concentration

from .test_concentration import catalog, security, workspace


def test_missing_account_basis_is_unavailable_even_when_workspace_position_nets_to_zero():
    netted = {**security(value=0), "quantity": 0, "account_ids": ["long", "short"]}
    with pytest.raises(ConcentrationUnavailable):
        project_portfolio_concentration(workspace([netted]), catalog(), holding_rows=[])


def test_empty_workspace_and_empty_account_basis_remain_a_valid_empty_exposure():
    result = project_portfolio_concentration(workspace([]), catalog(), holding_rows=[])
    assert result["status"] == "complete"
    assert result["scopes"][0]["rows"] == []
    assert result["scopes"][1]["rows"] == []
