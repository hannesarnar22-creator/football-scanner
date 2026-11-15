import pytest


@pytest.fixture(autouse=True)
def temp_database_url(tmp_path, monkeypatch):
    """Allar prófanir (líka Streamlit AppTest) nota tímabundinn gagnagrunn –
    raunverulegi gagnagrunnurinn í data/ er aldrei snertur."""
    url = f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url
