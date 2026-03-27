# Technical Documentation

## Overview

**Reservatórios Volume** downloads, processes, and visualizes the daily **EAR** (*Energia Armazenada* — Stored Energy) indicator published by the ONS (Operador Nacional do Sistema Elétrico). EAR measures how full Brazil's hydroelectric reservoirs are as a percentage of their maximum storage capacity, broken down by subsystem.

---

## Architecture

The application follows a simple three-stage pipeline:

```
Download → Prepare → Plot
```

### 1. Download (`download_data`)

- Iterates over a configurable year range (default 2000–2025).
- For each year, fetches a Parquet file from the ONS public S3 bucket:
  ```
  https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/ear_subsistema_di/EAR_DIARIO_SUBSISTEMA_{YEAR}.parquet
  ```
- **Caching**: completed (past) years are cached locally in a `.cache/` directory as Parquet files.  On subsequent runs these files are loaded from disk instead of being re-downloaded.  The current calendar year (and any future year) is always fetched from the network so that new daily records are included.
- Concatenates all years into a single `pandas.DataFrame`.
- Prints progress to stdout so the user can track long downloads.

### 2. Prepare (`prepare_data`)

| Step | Description |
|---|---|
| **Type coercion** | Converts `ear_verif_subsistema_percentual` to numeric; drops rows that fail. |
| **Date parsing** | Parses `ear_data` into a datetime; derives `ano` (year) and `dia_do_ano` (day-of-year 1–365). |
| **Aggregation** | Groups by `(ano, dia_do_ano, nom_subsistema)` and takes the mean EAR percentage. |
| **Smoothing** | Applies a **7-day trailing rolling average** per subsystem-year to reduce daily noise. |

### 3. Plot (`plot_reservoirs`)

- Creates a **2×2 matplotlib figure** (one subplot per subsystem).
- Each year is drawn as two overlapping lines:
  - A *translucent raw curve* (`alpha=0.25`) showing the daily values.
  - A *bold smooth curve* showing the 7-day moving average.
- For the **Sudeste/Centro-Oeste** subsystem, a dashed red line at **65 %** marks a common operational reference threshold.
- The figure can be saved to disk (`--output`) or displayed interactively.

---

## Caching

The download step uses a local Parquet cache (`.cache/` directory next to the script):

| Year status | Behaviour |
|---|---|
| **Past year** (year < current calendar year) | Read from cache if present; otherwise download and save to cache. |
| **Current or future year** | Always download from the network (data is still being updated). |

Pass `--no-cache` to disable caching entirely and force a full network download.

---

## Dataset Columns

The ONS Parquet files contain (among others) the following columns used by this tool:

| Column | Type | Description |
|---|---|---|
| `ear_data` | date/string | Date of the measurement |
| `nom_subsistema` | string | Name of the electrical subsystem |
| `ear_verif_subsistema_percentual` | float | Verified EAR as a percentage of maximum capacity |

---

## Subsystems

Brazil's electrical grid is divided into four major subsystems:

| Subsystem | Regions |
|---|---|
| **Sudeste / Centro-Oeste** | São Paulo, Rio de Janeiro, Minas Gerais, Goiás, Mato Grosso do Sul, etc. |
| **Sul** | Paraná, Santa Catarina, Rio Grande do Sul |
| **Nordeste** | Bahia, Pernambuco, Ceará, and other northeastern states |
| **Norte** | Amazonas, Pará, Tocantins, and other northern states |

---

## Dependencies

| Package | Purpose |
|---|---|
| `pandas` | Data manipulation and aggregation |
| `matplotlib` | Chart generation |
| `seaborn` | Plot styling (`whitegrid` theme) |
| `pyarrow` | Read Parquet files |
| `numpy` | Numeric operations (color maps, clipping) |
| `requests` | HTTP downloads |
| `pytest` | Test suite |

All versions are pinned to minimum compatible releases in `requirements.txt`.

---

## Testing

Run the full test suite:

```bash
python -m pytest tests/ -v
```

Tests cover argument parsing, data preparation, subsystem ordering, caching logic, download behaviour (with mocked network), plotting, and end-to-end integration.  All tests use `unittest.mock` and `tmp_path` fixtures so they run offline and leave no side effects.

---

## Extending the Tool

### Adding a new subsystem or reference line

In `plot_reservoirs`, locate the block that draws the 65 % reference line:

```python
if "Sudeste" in sub or "Centro" in sub:
    ax.axhline(y=65, ...)
```

Add additional conditions or reference levels as needed.

### Changing the color palette

Replace `plt.cm.tab10` with any matplotlib colormap (e.g., `plt.cm.Set2`, `plt.cm.Dark2`).

### Exporting data

After calling `prepare_data`, the resulting `DataFrame` can be exported:

```python
df_agg.to_csv("ear_aggregated.csv", index=False)
```
