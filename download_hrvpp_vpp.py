#!/usr/bin/env python3
"""
Download Copernicus CLMS HR-VPP VPP products for specified
Sentinel-2 tiles and years from WEkEO.

Dataset: EO:EEA:DAT:CLMS_HRVPP_VPP (Vegetation Phenology and Productivity
parameters, 10 m, Sentinel-2 tiling grid, 2017-present).

Access goes through the current WEkEO Harmonized Data Access (HDA) API using
the official `hda` Python client (https://hda.readthedocs.io).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from hda import Client, Configuration
    from hda.api import ConfigurationError, SearchResults
except ImportError:  # pragma: no cover - import guard only
    sys.exit("The 'hda' package is required. Install it with: pip install hda")


DATASET_ID = "EO:EEA:DAT:CLMS_HRVPP_VPP"

# The 14 VPP raster layers published per tile / year / season
# (13 phenology & productivity parameters + the QFLAG quality layer).
VPP_PRODUCT_TYPES = (
    "AMPL", "EOSD", "EOSV", "LENGTH", "LSLOPE", "MAXD", "MAXV",
    "MINV", "QFLAG", "RSLOPE", "SOSD", "SOSV", "SPROD", "TPROD",
)

# VPP publishes up to two growing seasons per year, as productGroupId s1 / s2.
VPP_SEASONS = ("s1", "s2")

# Product identifiers look like: VPP_2018_S2_T32VNM-010m_V101_s1_AMPL
PRODUCT_ID_RE = re.compile(
    r"^VPP_(?P<year>\d{4})_S2_T(?P<tile>[0-9]{2}[A-Z]{3})-(?P<res>\d+)m"
    r"_(?P<version>V\d+)_(?P<season>s\d)_(?P<product_type>[A-Z]+)$"
)

TILE_RE = re.compile(r"^T?(?P<tile>[0-9]{2}[A-Z]{3})$")

MANIFEST_NAME = "download_manifest.json"

logger = logging.getLogger("hrvpp")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def normalize_tile(tile: str) -> Tuple[str, str]:
    """Normalize a Sentinel-2 tile ID.

    Accepts ``32VNM`` or ``T32VNM`` (any case) and returns the pair
    ``("T32VNM", "32VNM")``: the display/directory form and the bare form
    used by the catalogue's ``tileId`` field.
    """
    match = TILE_RE.match(tile.strip().upper())
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid Sentinel-2 tile ID: {tile!r} (expected e.g. T32VNM or 32VNM)"
        )
    bare = match.group("tile")
    return f"T{bare}", bare


def normalize_season(season: Optional[str]) -> Optional[str]:
    """Map a user-supplied season to the catalogue's productGroupId (s1/s2)."""
    if season is None:
        return None
    value = season.strip().lower()
    if value in ("all", "both", ""):
        return None
    if value in ("1", "s1"):
        return "s1"
    if value in ("2", "s2"):
        return "s2"
    raise argparse.ArgumentTypeError(
        f"Invalid season: {season!r} (expected 1, 2, s1, s2 or all)"
    )


def human_size(num_bytes: Optional[int]) -> str:
    """Format a byte count for display; returns '?' when unknown."""
    if not isinstance(num_bytes, (int, float)) or num_bytes < 0:
        return "?"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}PB"


def parse_product_id(product_id: str) -> Dict[str, Any]:
    """Split a VPP product identifier into its metadata components.

    The identifier is the most reliable source of tile / year / season /
    parameter, so it is preferred over free-form catalogue properties.
    """
    match = PRODUCT_ID_RE.match(product_id or "")
    if not match:
        return {
            "year": None, "tile": None, "season": None,
            "product_type": None, "version": None,
        }
    parts = match.groupdict()
    return {
        "year": int(parts["year"]),
        "tile": f"T{parts['tile']}",
        "season": parts["season"],
        "product_type": parts["product_type"],
        "version": parts["version"],
    }


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------
def create_client(progress: bool = True) -> Client:
    """Build an authenticated HDA client.

    Credentials are resolved, in order, from:
      1. ``HDA_USER`` / ``HDA_PASSWORD`` (the official hda variables)
      2. ``WEKEO_USERNAME`` / ``WEKEO_PASSWORD``
      3. the official ``~/.hdarc`` configuration file
    Nothing is ever hard-coded in this script.
    """
    user = os.environ.get("HDA_USER") or os.environ.get("WEKEO_USERNAME")
    password = os.environ.get("HDA_PASSWORD") or os.environ.get("WEKEO_PASSWORD")
    try:
        config = Configuration(user=user, password=password)
    except ConfigurationError:
        raise SystemExit(
            "No WEkEO credentials found.\n"
            "Set them in the environment:\n"
            "    export WEKEO_USERNAME='...'\n"
            "    export WEKEO_PASSWORD='...'\n"
            "or create ~/.hdarc containing:\n"
            "    url: https://gateway.prod.wekeo2.eu/hda-broker/api/v1\n"
            "    user: your_username\n"
            "    password: your_password"
        )
    return Client(config=config, progress=progress)


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
def build_query(
    tile_query: str,
    year: int,
    season: Optional[str] = None,
    product_types: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Build one HDA search query for a single tile / year.

    Field names follow the current HDA queryable schema of
    EO:EEA:DAT:CLMS_HRVPP_VPP (``tileId``, ``productType``,
    ``productGroupId``, ``start``, ``end``).  Pagination fields are set by
    the client itself, so they are not included here.
    """
    query: Dict[str, Any] = {
        "dataset_id": DATASET_ID,
        "tileId": tile_query,
        "start": f"{year}-01-01T00:00:00.000Z",
        "end": f"{year}-12-31T23:59:59.999Z",
    }
    if season:
        query["productGroupId"] = season
    if product_types and len(product_types) == 1:
        # The catalogue accepts a single productType per request; several
        # types are handled by filtering the results client-side.
        query["productType"] = product_types[0]
    return query


def search_products(
    client: Client,
    tile_display: str,
    tile_query: str,
    year: int,
    season: Optional[str] = None,
    product_types: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Run the catalogue search for one year and return the raw results.

    If the bare tile form yields nothing, the ``T``-prefixed form is tried
    once, so the script works whichever spelling the catalogue expects.
    """
    for tile_value in (tile_query, tile_display):
        query = build_query(tile_value, year, season, product_types)
        logger.debug("Query: %s", json.dumps(query))
        try:
            matches = client.search(query)
        except Exception as exc:  # noqa: BLE001 - one bad year must not abort
            logger.error("Search failed for %s %s: %s", tile_display, year, exc)
            return []
        results = list(matches.results)
        if results:
            return results
        logger.debug("No results for tileId=%s in %s", tile_value, year)
    return []


def filter_products(
    raw_results: Iterable[Dict[str, Any]],
    tile_display: str,
    start_year: int,
    end_year: int,
    season: Optional[str] = None,
    product_types: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Normalize, filter and de-duplicate catalogue results.

    The catalogue's temporal filter is inclusive at both ends, so products
    of a neighbouring year can be returned; everything is therefore
    re-checked here against the parsed product identifier.  De-duplication
    uses that identifier, which is the catalogue's unique product key.
    """
    wanted_types = {t.upper() for t in product_types} if product_types else None
    seen: set = set()
    products: List[Dict[str, Any]] = []

    for result in raw_results:
        product_id = result.get("id")
        props = result.get("properties") or {}
        if not product_id:
            logger.warning("Skipping catalogue entry without an id: %s", props)
            continue

        meta = parse_product_id(product_id)
        # Fall back to catalogue properties if the identifier is unexpected.
        product_type = meta["product_type"] or props.get("productType")
        product_season = meta["season"] or props.get("productGroupId")
        year = meta["year"]
        tile = meta["tile"] or (
            f"T{props['tileId']}" if props.get("tileId") else None
        )

        if year is None or tile is None:
            logger.warning("Cannot parse product %s, skipping", product_id)
            continue
        if tile != tile_display:
            continue
        if not start_year <= year <= end_year:
            continue
        if season and product_season != season:
            continue
        if wanted_types and (product_type or "").upper() not in wanted_types:
            continue
        if product_id in seen:
            logger.debug("Duplicate product %s ignored", product_id)
            continue
        seen.add(product_id)

        location = props.get("location") or ""
        filename = (
            os.path.basename(location)
            if location.lower().endswith(".tif")
            else f"{product_id}.tif"
        )
        size = props.get("size")

        products.append(
            {
                "product_id": product_id,
                "filename": filename,
                "year": year,
                "tile": tile,
                "season": product_season,
                "product_type": product_type,
                "version": meta["version"],
                "size": size if isinstance(size, int) else None,
                "result": result,
            }
        )

    products.sort(
        key=lambda p: (p["year"], p["season"] or "", p["product_type"] or "")
    )
    return products


def print_products(products: Sequence[Dict[str, Any]]) -> None:
    """Print the matched products as a readable table."""
    if not products:
        logger.info("No products matched the given criteria")
        return
    header = f"{'YEAR':<6}{'TILE':<8}{'SEASON':<8}{'TYPE':<8}{'SIZE':>10}  FILE"
    print(header)
    print("-" * len(header))
    for p in products:
        print(
            f"{p['year']:<6}{p['tile']:<8}{(p['season'] or '-'):<8}"
            f"{(p['product_type'] or '-'):<8}{human_size(p['size']):>10}  "
            f"{p['filename']}   [{p['product_id']}]"
        )
    total = sum(p["size"] for p in products if p["size"])
    print("-" * len(header))
    print(f"{len(products)} product(s), total volume ~{human_size(total)}")


# --------------------------------------------------------------------------
# Manifest (makes re-runs reliable regardless of server-side file naming)
# --------------------------------------------------------------------------
def load_manifest(tile_dir: str) -> Dict[str, Any]:
    path = os.path.join(tile_dir, MANIFEST_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("Could not read manifest %s: %s", path, exc)
        return {}


def save_manifest(tile_dir: str, manifest: Dict[str, Any]) -> None:
    path = os.path.join(tile_dir, MANIFEST_NAME)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=1, sort_keys=True)
    except OSError as exc:
        logger.warning("Could not write manifest %s: %s", path, exc)


def existing_complete_file(
    year_dir: str, product: Dict[str, Any], manifest: Dict[str, Any]
) -> Optional[str]:
    """Return the path of an already complete local file, else None.

    A file counts as complete when it exists and either matches the size
    reported by the catalogue, or the catalogue reported no size at all.
    """
    candidates = [product["filename"]]
    recorded = manifest.get(product["product_id"], {}).get("filename")
    if recorded and recorded not in candidates:
        candidates.append(recorded)

    expected = product["size"]
    for name in candidates:
        path = os.path.join(year_dir, name)
        if not os.path.isfile(path):
            continue
        actual = os.path.getsize(path)
        if expected is None or actual == expected:
            return path
        logger.info(
            "Re-downloading %s: %d bytes on disk, %d expected",
            name, actual, expected,
        )
    return None


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------
def download_product(
    client: Client,
    product: Dict[str, Any],
    year_dir: str,
    manifest: Dict[str, Any],
) -> Tuple[str, Optional[str]]:
    """Download a single product into ``year_dir``.

    Returns ``(status, message)`` where status is 'skipped', 'ok' or 'failed'.
    Never raises: the failure of one product must not stop the whole run.
    """
    os.makedirs(year_dir, exist_ok=True)

    existing = existing_complete_file(year_dir, product, manifest)
    if existing:
        logger.info("Skipping existing file %s", os.path.basename(existing))
        return "skipped", None

    before = set(os.listdir(year_dir))
    try:
        # A one-element SearchResults keeps the public hda download path
        # (order request + streaming, with the client's own progress bar).
        single = SearchResults(client, [product["result"]], DATASET_ID)
        single.download(download_dir=year_dir)
    except Exception as exc:  # noqa: BLE001 - one bad product must not abort
        return "failed", f"{type(exc).__name__}: {exc}"

    # hda's batch download logs worker exceptions instead of raising them,
    # so the result is verified on disk.
    after = set(os.listdir(year_dir))
    new_files = sorted(after - before)
    expected = product["filename"]
    candidates = [expected] if expected in after else new_files

    for name in candidates:
        path = os.path.join(year_dir, name)
        actual = os.path.getsize(path)
        if product["size"] is not None and actual != product["size"]:
            continue
        manifest[product["product_id"]] = {
            "filename": name,
            "size": actual,
            "year": product["year"],
            "season": product["season"],
            "product_type": product["product_type"],
        }
        logger.info("Downloaded %s (%s)", name, human_size(actual))
        return "ok", None

    if new_files:
        return "failed", (
            f"incomplete download: expected {product['size']} bytes for "
            f"{expected}, got {new_files}"
        )
    return "failed", f"no file was written for {product['product_id']}"


def download_all(
    client: Client,
    products: Sequence[Dict[str, Any]],
    output_dir: str,
    tile_display: str,
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """Download every product into OUTPUT/<TILE>/<YEAR>/.

    Returns ``(downloaded, skipped, failures)``.
    """
    tile_dir = os.path.join(output_dir, tile_display)
    os.makedirs(tile_dir, exist_ok=True)
    manifest = load_manifest(tile_dir)

    downloaded = 0
    skipped = 0
    failures: List[Tuple[str, str]] = []

    for index, product in enumerate(products, start=1):
        year_dir = os.path.join(tile_dir, str(product["year"]))
        logger.info(
            "[%d/%d] %s (%s)",
            index, len(products), product["filename"], human_size(product["size"]),
        )
        status, message = download_product(client, product, year_dir, manifest)
        if status == "ok":
            downloaded += 1
            save_manifest(tile_dir, manifest)
        elif status == "skipped":
            skipped += 1
        else:
            failures.append((product["product_id"], message or "unknown error"))
            logger.error("Failed %s: %s", product["product_id"], message)

    save_manifest(tile_dir, manifest)
    return downloaded, skipped, failures


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download Copernicus CLMS HR-VPP VPP products "
            f"({DATASET_ID}) from WEkEO for a Sentinel-2 tile."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--tile", required=True,
        help="Sentinel-2 tile ID, e.g. T32VNM (32VNM is also accepted)",
    )
    parser.add_argument("--start-year", type=int, required=True, help="First year")
    parser.add_argument("--end-year", type=int, required=True, help="Last year")
    parser.add_argument(
        "--output", default="./HRVPP_VPP",
        help="Output directory; files go to OUTPUT/<TILE>/<YEAR>/",
    )
    parser.add_argument(
        "--season", default=None,
        help="Growing season to keep: 1, 2, s1, s2 or all",
    )
    parser.add_argument(
        "--product-type", action="append", default=None, metavar="TYPE",
        help=(
            "Keep only this VPP parameter; repeatable. One of: "
            + ", ".join(VPP_PRODUCT_TYPES)
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Only search and list the matching products, download nothing",
    )
    parser.add_argument(
        "--no-progress", action="store_true", help="Disable the progress bar"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    try:
        tile_display, tile_query = normalize_tile(args.tile)
        season = normalize_season(args.season)
    except argparse.ArgumentTypeError as exc:
        logger.error("%s", exc)
        return 2

    if args.start_year > args.end_year:
        logger.error("--start-year must not be greater than --end-year")
        return 2

    product_types: Optional[List[str]] = None
    if args.product_type:
        product_types = [t.upper() for t in args.product_type]
        unknown = [t for t in product_types if t not in VPP_PRODUCT_TYPES]
        if unknown:
            logger.error(
                "Unknown product type(s): %s. Valid values: %s",
                ", ".join(unknown), ", ".join(VPP_PRODUCT_TYPES),
            )
            return 2

    logger.info("Dataset: %s", DATASET_ID)
    logger.info("Tile: %s", tile_display)
    logger.info("Years: %d-%d", args.start_year, args.end_year)
    logger.info("Season: %s", season or "all")
    if product_types:
        logger.info("Product types: %s", ", ".join(product_types))
    logger.info("Output: %s", os.path.abspath(args.output))

    client = create_client(progress=not args.no_progress)

    logger.info("Searching products...")
    raw_results: List[Dict[str, Any]] = []
    for year in range(args.start_year, args.end_year + 1):
        year_results = search_products(
            client, tile_display, tile_query, year, season, product_types
        )
        logger.info("  %d: %d catalogue entries", year, len(year_results))
        raw_results.extend(year_results)

    products = filter_products(
        raw_results, tile_display, args.start_year, args.end_year,
        season, product_types,
    )
    logger.info("Found %d products", len(products))
    print_products(products)

    if not products:
        return 0

    if args.dry_run:
        logger.info("Dry run: nothing will be downloaded")
        for product in products:
            logger.info(
                "Would download %s -> %s",
                product["filename"],
                os.path.join(
                    os.path.abspath(args.output), tile_display,
                    str(product["year"]), product["filename"],
                ),
            )
        return 0

    logger.info("Downloading %d products...", len(products))
    downloaded, skipped, failures = download_all(
        client, products, args.output, tile_display
    )

    logger.info(
        "Done. downloaded=%d skipped=%d failed=%d",
        downloaded, skipped, len(failures),
    )
    for product_id, message in failures:
        logger.error("  %s: %s", product_id, message)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
