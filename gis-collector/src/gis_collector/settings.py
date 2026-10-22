"""Runtime settings. Everything that is *behaviour* rather than *source description*.

Source descriptions live in ``specs/*.json``; this object holds knobs that the
specs do not (and should not) contain: timeouts, concurrency, per-source policies.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_USER_AGENT = "GIS-Collector-Bot/1.0 (contact: admin@vimgip.gov.ua)"
CHERNIHIV_BBOX = (30.49, 50.46, 33.50, 52.38)  # west, south, east, north


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    # --- HTTP ---------------------------------------------------------------
    user_agent: str = DEFAULT_USER_AGENT
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 90.0
    max_retries: int = 4
    backoff_base_s: float = 1.0
    backoff_max_s: float = 60.0
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    default_requests_per_second: float = 5.0
    host_requests_per_second: dict[str, float] = Field(
        default_factory=lambda: {
            "overpass-api.de": 0.5,  # public instance: be polite, 2 slots per IP
            "nominatim.openstreetmap.org": 1.0,  # usage policy: max 1 req/s
        }
    )
    max_workers: int = 8
    max_error_ratio: float = 0.2  # abort an endpoint when more sub-requests fail

    # --- Region / boundaries ------------------------------------------------
    default_region_name: str = "Чернігівська область"
    region_relation_ids: dict[str, int] = Field(
        default_factory=lambda: {"Чернігівська область": 71249}
    )
    region_bbox: tuple[float, float, float, float] = CHERNIHIV_BBOX
    boundary_file: Path | None = None  # GeoJSON with the region polygon (offline mode)
    allow_bbox_boundary_fallback: bool = True
    #: Sources whose spec only mentions the BBOX in ``notes`` but where an exact
    #: point-in-polygon filter is required (neighbouring regions / Belarus).
    region_filter_sources: frozenset[str] = frozenset({"ecozagroza_chernihiv"})

    # --- Parameter variants -------------------------------------------------
    #: Sources for which ``second_sample_params`` describe a *second real dataset*
    #: (ecozagroza: air_pollution layer, earthdata: VNP14 product) and must be
    #: collected too. For the others the second sample is only a test query.
    param_variant_sources: frozenset[str] = frozenset(
        {"ecozagroza_chernihiv", "earthdata_chernihiv"}
    )
    max_pages: int = 10  # hard cap for page_number pagination
    max_index_pages: int = 500  # hard cap for cursor pagination

    # --- Overpass -----------------------------------------------------------
    overpass_check_status: bool = True
    overpass_max_slot_wait_s: float = 60.0
    overpass_attempts: int = 3  # retries for "runtime error" remarks in 200 responses
    skip_untagged_elements: bool = True  # drops skeleton nodes from ``out skel``

    # --- Tiles --------------------------------------------------------------
    default_tile_zoom: int = 11
    tile_zoom_by_layer: dict[str, int] = Field(
        default_factory=lambda: {
            "land_polygons": 11,
            "land_sales_points": 11,
            "auction_points": 8,  # spec notes: z8 does not drop points
        }
    )
    tile_id_fields: dict[str, str] = Field(
        default_factory=lambda: {
            "land_polygons": "cadnum",
            "auction_points": "auction_id",
            "land_sales_points": "public_url",
        }
    )
    max_tiles: int = 50_000
    use_style_zoom_limits: bool = True

    # --- DREAM --------------------------------------------------------------
    dream_max_items: int | None = None  # cap detail requests (debug / smoke runs)

    # --- Templated endpoints ({cadnum}) --------------------------------------
    #: Explicit values per source: ``{"kadastrova_karta": [{"cadnum": "..."}]}``.
    template_values: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    #: Enrich up to N cadastral numbers discovered by the tile endpoints (0 = off).
    template_enrich_limit: int = 0
    #: Use ``second_sample_params`` as values when nothing else is available.
    template_use_samples: bool = False

    # --- Auth ---------------------------------------------------------------
    tokens: dict[str, str] = Field(default_factory=dict)
    token_env_vars: dict[str, str] = Field(
        default_factory=lambda: {"earthdata_chernihiv": "EARTHDATA_TOKEN"}
    )

    # --- Output -------------------------------------------------------------
    include_meta: bool = True  # adds properties["_meta"]
    coordinate_precision: int = 7  # decimal places (~1 cm)
    strict: bool = False  # raise on the first failed endpoint instead of reporting
