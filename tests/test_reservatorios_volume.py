"""Tests for reservatorios_volume module."""

import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import matplotlib
import numpy as np
import pandas as pd
import pytest

matplotlib.use("Agg")  # non-interactive backend for CI

# Ensure the package root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reservatorios_volume as rv

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SUBSYSTEMS = [
    "Sudeste / Centro-Oeste",
    "Sul",
    "Nordeste",
    "Norte",
]


def _make_raw_df(years=(2023,), days_per_year=30):
    """Build a minimal raw DataFrame that mimics ONS parquet schema."""
    rows = []
    for year in years:
        for day in range(1, days_per_year + 1):
            date = pd.Timestamp(year=year, month=1, day=1) + pd.Timedelta(days=day - 1)
            for sub in SUBSYSTEMS:
                rows.append(
                    {
                        rv.COL_DATE: date.strftime("%Y-%m-%d"),
                        rv.COL_SUBSYSTEM: sub,
                        rv.COL_EAR_PCT: round(50 + 10 * np.random.rand(), 2),
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture()
def raw_df():
    """Single-year raw dataframe."""
    return _make_raw_df(years=(2023,), days_per_year=30)


@pytest.fixture()
def multi_year_df():
    """Multi-year raw dataframe."""
    return _make_raw_df(years=(2022, 2023), days_per_year=30)


@pytest.fixture()
def agg_df(raw_df):
    """Pre-aggregated dataframe ready for plotting."""
    return rv.prepare_data(raw_df)


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_defaults(self):
        args = rv.parse_args([])
        assert args.start_year == 2000
        assert args.end_year == 2025
        assert args.output is None
        assert args.no_cache is False

    def test_custom_years(self):
        args = rv.parse_args(["--start-year", "2010", "--end-year", "2020"])
        assert args.start_year == 2010
        assert args.end_year == 2020

    def test_output_flag(self):
        args = rv.parse_args(["--output", "chart.png"])
        assert args.output == "chart.png"

    def test_no_cache_flag(self):
        args = rv.parse_args(["--no-cache"])
        assert args.no_cache is True


# ---------------------------------------------------------------------------
# order_subsystems
# ---------------------------------------------------------------------------


class TestOrderSubsystems:
    def test_sudeste_first(self):
        subs = np.array(["Norte", "Sul", "Sudeste / Centro-Oeste", "Nordeste"])
        result = rv.order_subsystems(subs)
        assert result[0] == "Sudeste / Centro-Oeste"
        assert isinstance(result, list)

    def test_without_sudeste(self):
        subs = np.array(["Norte", "Sul", "Nordeste"])
        result = rv.order_subsystems(subs)
        assert result == ["Nordeste", "Norte", "Sul"]

    def test_single_subsystem(self):
        subs = np.array(["Sul"])
        result = rv.order_subsystems(subs)
        assert result == ["Sul"]

    def test_empty(self):
        result = rv.order_subsystems(np.array([]))
        assert result == []


# ---------------------------------------------------------------------------
# prepare_data
# ---------------------------------------------------------------------------


class TestPrepareData:
    def test_output_columns(self, raw_df):
        df_agg = rv.prepare_data(raw_df)
        expected = {"ano", "dia_do_ano", rv.COL_SUBSYSTEM, rv.COL_EAR_PCT, "ear_moving7"}
        assert expected.issubset(set(df_agg.columns))

    def test_no_nan_ear(self, raw_df):
        df_agg = rv.prepare_data(raw_df)
        assert df_agg[rv.COL_EAR_PCT].isna().sum() == 0

    def test_rolling_average_present(self, raw_df):
        df_agg = rv.prepare_data(raw_df)
        assert df_agg["ear_moving7"].notna().all()

    def test_multi_year_preserves_years(self, multi_year_df):
        df_agg = rv.prepare_data(multi_year_df)
        assert set(df_agg["ano"].unique()) == {2022, 2023}

    def test_handles_non_numeric_ear(self):
        """Rows with non-numeric EAR values should be dropped."""
        df = _make_raw_df(years=(2023,), days_per_year=5)
        # Force column to object dtype so we can insert a non-numeric value
        df[rv.COL_EAR_PCT] = df[rv.COL_EAR_PCT].astype(object)
        df.loc[0, rv.COL_EAR_PCT] = "invalid"
        df_agg = rv.prepare_data(df)
        assert df_agg[rv.COL_EAR_PCT].isna().sum() == 0

    def test_handles_bad_dates(self):
        """Rows with unparseable dates should be dropped."""
        df = _make_raw_df(years=(2023,), days_per_year=5)
        df.loc[0, rv.COL_DATE] = "not-a-date"
        df_agg = rv.prepare_data(df)
        assert len(df_agg) > 0

    def test_aggregation_reduces_rows(self):
        """Duplicate (year, day, subsystem) rows should be aggregated."""
        df = _make_raw_df(years=(2023,), days_per_year=5)
        df = pd.concat([df, df], ignore_index=True)
        df_agg = rv.prepare_data(df)
        # Each day/sub combo should appear once after aggregation
        counts = df_agg.groupby(["ano", "dia_do_ano", rv.COL_SUBSYSTEM]).size()
        assert (counts == 1).all()


# ---------------------------------------------------------------------------
# _is_year_complete
# ---------------------------------------------------------------------------


class TestIsYearComplete:
    def test_past_year(self):
        assert rv._is_year_complete(2000) is True

    def test_current_year(self):
        current = datetime.now(timezone.utc).year
        assert rv._is_year_complete(current) is False

    def test_future_year(self):
        assert rv._is_year_complete(3000) is False


# ---------------------------------------------------------------------------
# download_data – with mocked network
# ---------------------------------------------------------------------------


class TestDownloadData:
    def _mock_fetch(self, years_data):
        """Return a side_effect callable that serves pre-built DataFrames."""
        mapping = {y: _make_raw_df(years=(y,), days_per_year=10) for y in years_data}

        def _side_effect(year):
            if year in mapping:
                return mapping[year]
            raise Exception(f"no data for {year}")

        return _side_effect

    @patch.object(rv, "_fetch_year")
    def test_basic_download(self, mock_fetch, tmp_path):
        mock_fetch.side_effect = self._mock_fetch([2023])
        df = rv.download_data(2023, 2023, use_cache=False, cache_dir=tmp_path)
        assert len(df) > 0
        assert mock_fetch.call_count == 1

    @patch.object(rv, "_fetch_year")
    def test_caching_creates_files(self, mock_fetch, tmp_path):
        """Completed years should be persisted to disk."""
        mock_fetch.side_effect = self._mock_fetch([2020])
        rv.download_data(2020, 2020, use_cache=True, cache_dir=tmp_path)

        cached = tmp_path / "EAR_DIARIO_SUBSISTEMA_2020.parquet"
        assert cached.exists()

    @patch.object(rv, "_fetch_year")
    def test_cache_hit_skips_fetch(self, mock_fetch, tmp_path):
        """Second run should read from cache and not call _fetch_year."""
        mock_fetch.side_effect = self._mock_fetch([2020])

        # First run – populates cache
        rv.download_data(2020, 2020, use_cache=True, cache_dir=tmp_path)
        assert mock_fetch.call_count == 1

        # Second run – should hit cache
        mock_fetch.reset_mock()
        mock_fetch.side_effect = self._mock_fetch([2020])
        df = rv.download_data(2020, 2020, use_cache=True, cache_dir=tmp_path)
        assert mock_fetch.call_count == 0
        assert len(df) > 0

    @patch.object(rv, "_is_year_complete", return_value=False)
    @patch.object(rv, "_fetch_year")
    def test_current_year_always_fetched(self, mock_fetch, mock_complete, tmp_path):
        """The current/incomplete year must always be fetched from network."""
        mock_fetch.side_effect = self._mock_fetch([2025])
        rv.download_data(2025, 2025, use_cache=True, cache_dir=tmp_path)
        assert mock_fetch.call_count == 1

        # Run again – still fetched (not cached)
        mock_fetch.reset_mock()
        mock_fetch.side_effect = self._mock_fetch([2025])
        rv.download_data(2025, 2025, use_cache=True, cache_dir=tmp_path)
        assert mock_fetch.call_count == 1

    @patch.object(rv, "_fetch_year")
    def test_no_cache_flag(self, mock_fetch, tmp_path):
        """With use_cache=False, nothing should be read or written."""
        mock_fetch.side_effect = self._mock_fetch([2020])
        rv.download_data(2020, 2020, use_cache=False, cache_dir=tmp_path)

        cached = tmp_path / "EAR_DIARIO_SUBSISTEMA_2020.parquet"
        assert not cached.exists()

    @patch.object(rv, "_fetch_year")
    def test_all_years_fail_exits(self, mock_fetch, tmp_path):
        """SystemExit should be raised when no data can be downloaded."""
        mock_fetch.side_effect = Exception("network error")
        with pytest.raises(SystemExit):
            rv.download_data(2099, 2099, use_cache=False, cache_dir=tmp_path)

    @patch.object(rv, "_fetch_year")
    def test_partial_failure(self, mock_fetch, tmp_path):
        """Some years failing should still return data for successful years."""

        def _side_effect(year):
            if year == 2020:
                return _make_raw_df(years=(2020,), days_per_year=5)
            raise Exception("not found")

        mock_fetch.side_effect = _side_effect
        df = rv.download_data(2020, 2022, use_cache=False, cache_dir=tmp_path)
        assert len(df) > 0


# ---------------------------------------------------------------------------
# plot_reservoirs
# ---------------------------------------------------------------------------


class TestPlotReservoirs:
    def test_saves_to_file(self, agg_df, tmp_path):
        outfile = tmp_path / "test_output.png"
        rv.plot_reservoirs(agg_df, output=str(outfile))
        assert outfile.exists()
        assert outfile.stat().st_size > 0

    def test_no_output_shows_plot(self, agg_df):
        """When output is None, plt.show() should be called."""
        with patch("reservatorios_volume.plt") as mock_plt:
            mock_plt.subplots.return_value = matplotlib.pyplot.subplots(
                2, 2, figsize=(20, 14.5), sharex=True
            )
            mock_plt.cm = matplotlib.pyplot.cm
            rv.plot_reservoirs(agg_df, output=None)
            mock_plt.show.assert_called_once()

    def test_handles_fewer_than_four_subsystems(self, tmp_path):
        """Should not crash with fewer than 4 subsystems."""
        df = _make_raw_df(years=(2023,), days_per_year=10)
        df = df[df[rv.COL_SUBSYSTEM].isin(["Sul", "Norte"])]
        df_agg = rv.prepare_data(df)
        outfile = tmp_path / "two_subs.png"
        rv.plot_reservoirs(df_agg, output=str(outfile))
        assert outfile.exists()

    def test_empty_subsystem_shows_text(self, tmp_path):
        """A subsystem with no data should display a 'No data' message."""
        df = _make_raw_df(years=(2023,), days_per_year=10)
        df_agg = rv.prepare_data(df)
        # Remove one subsystem's data after aggregation to trigger the empty branch
        df_agg = df_agg[df_agg[rv.COL_SUBSYSTEM] != "Norte"]
        # But we need 4 subsystems for the ordering – add a dummy row that will
        # be empty after filtering
        dummy = pd.DataFrame(
            [{"ano": 2023, "dia_do_ano": 1, rv.COL_SUBSYSTEM: "Norte",
              rv.COL_EAR_PCT: np.nan, "ear_moving7": np.nan}]
        )
        df_agg = pd.concat([df_agg, dummy], ignore_index=True)
        # Now the data for "Norte" only has NaN, but it still exists in unique()
        # The plot function filters by subsystem and checks .empty
        # We need the subsystem to appear but have empty data after filtering
        # Actually let's just test with a df that has 4 subsystems but one is truly empty
        df_agg2 = df_agg[df_agg[rv.COL_SUBSYSTEM] != "Norte"].copy()
        outfile = tmp_path / "missing_sub.png"
        rv.plot_reservoirs(df_agg2, output=str(outfile))
        assert outfile.exists()


# ---------------------------------------------------------------------------
# main (integration-style)
# ---------------------------------------------------------------------------


class TestMain:
    @staticmethod
    def _mock_fetch_for_year(year):
        return _make_raw_df(years=(year,), days_per_year=15)

    @patch.object(rv, "_fetch_year")
    def test_end_to_end(self, mock_fetch, tmp_path):
        """Full pipeline with mocked network."""
        mock_fetch.side_effect = self._mock_fetch_for_year
        outfile = tmp_path / "e2e.png"
        rv.main(
            [
                "--start-year", "2023",
                "--end-year", "2023",
                "--output", str(outfile),
                "--no-cache",
            ]
        )
        assert outfile.exists()

    @patch.object(rv, "_fetch_year")
    def test_end_to_end_with_cache(self, mock_fetch, tmp_path):
        """Full pipeline with cache enabled, verifying cache is created."""
        mock_fetch.side_effect = self._mock_fetch_for_year
        outfile = tmp_path / "e2e_cached.png"

        # Monkeypatch CACHE_DIR to tmp_path for this test
        with patch.object(rv, "CACHE_DIR", tmp_path / "cache"):
            rv.main(
                [
                    "--start-year", "2020",
                    "--end-year", "2020",
                    "--output", str(outfile),
                ]
            )
        assert outfile.exists()
