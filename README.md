# HR-VPP VPP downloader

Download Copernicus CLMS **HR-VPP Vegetation Phenology and Productivity
Parameters (VPP)** for a given Sentinel-2 tile and year range from
[WEkEO](https://www.wekeo.eu/), using the current Harmonized Data Access (HDA)
API and the official [`hda`](https://hda.readthedocs.io) Python client.

* Dataset ID: `EO:EEA:DAT:CLMS_HRVPP_VPP`
* Resolution: 10 m, Sentinel-2 tiling grid (UTM/WGS84)
* Coverage: EEA38 + United Kingdom, 2017 → present

## What gets downloaded

The VPP product is **not** one file per tile per year. For every tile and year
the catalogue publishes **28 GeoTIFFs**: 14 layers (13 phenology and
productivity parameters plus the `QFLAG` quality layer) for each of the
**two growing seasons** (`s1`, `s2`):

```
AMPL  EOSD  EOSV  LENGTH  LSLOPE  MAXD  MAXV
MINV  QFLAG RSLOPE SOSD   SOSV    SPROD TPROD
```

A single tile-year is roughly 4–6 GB, so a full 2017–2024 run for one tile is
in the 40 GB range. Use `--dry-run` first, and `--product-type` /`--season` to
narrow the request.

Files are written as:

```
OUTPUT/
  T32VNM/
    download_manifest.json
    2017/
      VPP_2017_S2_T32VNM-010m_V101_s1_AMPL.tif
      VPP_2017_S2_T32VNM-010m_V101_s1_EOSD.tif
      ...
    2018/
    ...
```

`download_manifest.json` records which product ID maps to which local file, so
re-runs resume correctly even if the server chooses a different file name.

## Installation

Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` pins only `hda`; it pulls in `requests` and `tqdm` (the
progress bar) itself.

## WEkEO authentication

You need a free WEkEO account: <https://www.wekeo.eu/register>.
Credentials are never stored in the script. It looks for them in this order:

1. `HDA_USER` / `HDA_PASSWORD` (the official `hda` variables)
2. `WEKEO_USERNAME` / `WEKEO_PASSWORD`
3. `~/.hdarc`

Environment variables:

```bash
export WEKEO_USERNAME="your_username"
export WEKEO_PASSWORD="your_password"
```

Windows PowerShell:

```powershell
$env:WEKEO_USERNAME = "your_username"
$env:WEKEO_PASSWORD = "your_password"
```

Or the official config file `~/.hdarc`:

```
url: https://gateway.prod.wekeo2.eu/hda-broker/api/v1
user: your_username
password: your_password
```

The first request implicitly accepts the Copernicus Land Monitoring Service
data policy on your behalf, which is what the WEkEO portal does too.

## Usage

Single tile, one year:

```bash
python download_hrvpp_vpp.py --tile T32VNM --start-year 2020 --end-year 2020 --output ./HRVPP_VPP
```

Multiple years (the tile may be given with or without the leading `T`):

```bash
python download_hrvpp_vpp.py --tile 32VNM --start-year 2017 --end-year 2024 --output /data/HRVPP/VPP
```

Dry run — search and list only, download nothing:

```bash
python download_hrvpp_vpp.py --tile T32VNM --start-year 2020 --end-year 2024 --output ./HRVPP_VPP --dry-run
```

Only the first season, and only the phenology dates:

```bash
python download_hrvpp_vpp.py --tile 32VNM --start-year 2017 --end-year 2024 --season 1 --product-type SOSD --product-type EOSD --output ./HRVPP_VPP
```

### Options

| Option | Description |
| --- | --- |
| `--tile` | Sentinel-2 tile, `T32VNM` or `32VNM` (required) |
| `--start-year` / `--end-year` | Inclusive year range (required) |
| `--output` | Output root; files go to `OUTPUT/<TILE>/<YEAR>/` (default `./HRVPP_VPP`) |
| `--season` | `1`, `2`, `s1`, `s2` or `all` (default: both seasons) |
| `--product-type` | Keep only this VPP layer; repeatable (default: all 14) |
| `--dry-run` | Search and list, download nothing |
| `--no-progress` | Disable the progress bar |
| `--verbose` / `-v` | Debug logging, including the exact HDA query sent |

Exit codes: `0` success, `1` at least one file failed, `2` invalid arguments.

## Behaviour worth knowing

* **Resumable.** A file that already exists with the size reported by the
  catalogue is skipped and never overwritten. A truncated file (interrupted
  run) has the wrong size and is re-downloaded.
* **One failure does not stop the run.** Each product is downloaded
  independently; failures are logged with their error and listed again in the
  summary (`downloaded=… skipped=… failed=…`). Just re-run the same command to
  retry only what is missing.
* **De-duplication** uses the catalogue product identifier
  (e.g. `VPP_2018_S2_T32VNM-010m_V101_s1_AMPL`), not the file name.
* **Year filtering is re-checked locally.** The HDA temporal filter is
  inclusive at both ends, so a query for one year can return products of the
  neighbouring year; the script re-validates every result against the year
  parsed from the product identifier.
* **Product versions differ by year** (`V101` for the early years, `V105` for
  the recent ones). The script does not pin a version — it takes whatever the
  catalogue currently publishes.

## Query fields used

Verified against the live HDA queryable schema for
`EO:EEA:DAT:CLMS_HRVPP_VPP` (`GET
https://gateway.prod.wekeo2.eu/hda-broker/api/v1/dataaccess/queryable/EO%3AEEA%3ADAT%3ACLMS_HRVPP_VPP`,
requires a token) and against the underlying CLMS catalogue metadata:

```json
{
  "dataset_id": "EO:EEA:DAT:CLMS_HRVPP_VPP",
  "tileId": "32VNM",
  "start": "2020-01-01T00:00:00.000Z",
  "end": "2020-12-31T23:59:59.999Z",
  "productGroupId": "s1",
  "productType": "SOSD"
}
```

`itemsPerPage` / `startIndex` are set by the `hda` client, which paginates the
search automatically. Downloads use the client's standard two-step flow
(`dataaccess/download` order, then streaming of the returned `download_id`).

## Troubleshooting

**`No WEkEO credentials found`**
Neither the environment variables nor `~/.hdarc` were readable. Check for
typos and, on `~/.hdarc`, that the format is `key: value` per line.

**`401 Unauthorized` / `Invalid or missed token`**
Wrong username or password, or the account has not confirmed its email.
Log in once at <https://www.wekeo.eu/> to verify the account is active.

**`Found 0 products`**
Most often the tile is outside the HR-VPP coverage (EEA38 + UK only), or the
year is not published yet — the current year is typically only released the
following spring. Re-run with `-v` to see the exact query. The script already
tries both `32VNM` and `T32VNM` spellings of the tile.

**`Max quota reached. Please wait…`**
WEkEO applies a per-account request quota. Wait for the reset time in the
message and re-run; already-downloaded files will be skipped.

**Downloads are slow or time out**
HDA stages products before serving them, so the first request for an old
product can hang for a while. The `hda` client retries automatically. If a run
is interrupted, just start it again.

**`incomplete download: expected N bytes`**
The transfer was cut short. The partial file stays on disk, is detected as the
wrong size on the next run, and is re-downloaded.

## Licence

The data is distributed under the
[Copernicus Land Monitoring Service data policy](https://land.copernicus.eu/en/data-policy).
