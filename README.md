# gis-collector

Парсер открытых ГИС-источников по Черниговской области. Читает JSON-спецификации из
`specs/`, скачивает данные (JSON, GeoJSON, MVT/PBF, Overpass) и возвращает **единый список объектов**:

```python
{"source_id": "overpass", "geometry": {...GeoJSON, WGS84...} | None, "properties": {...}}
```

Парсер **не работает с базой данных** — сохранением занимается вызывающий код.

## Быстрый старт

```bash
python3.12 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

gis-collector plan                                         # какая стратегия обрабатывает какой endpoint
gis-collector collect -o out.json                          # все источники → список объектов
gis-collector collect --source ecozagroza_chernihiv --format geojson -o eco.geojson
gis-collector collect --exclude osm_chernihiv --workers 8 --log-level DEBUG
```

Из кода:

```python
from pathlib import Path
from gis_collector import GisCollector, Settings, SpecRepository, configure_logging

configure_logging("INFO")
specs = SpecRepository(Path("specs")).load_all()
with GisCollector(Settings()) as collector:
    features = collector.collect(specs)                    # list[Feature]
rows = [f.model_dump() for f in features]                  # [{"source_id", "geometry", "properties"}]

report = collector.run(specs)                              # + отчёт по каждому endpoint
print(report.failed)
```

Коды выхода CLI: `0` — всё успешно, `3` — часть endpoint-ов упала (данные остальных сохранены),
`2` — фатальная ошибка (нет specs, неверные аргументы).

## Структура

```
specs/                       JSON-спецификации (6 источников)
src/gis_collector/
  models.py                  pydantic: SourceSpec/EndpointSpec (вход) и Feature (выход)
  settings.py                все «ручки»: таймауты, ретраи, воркеры, политики по источникам
  spec_loader.py             загрузка/валидация specs, предупреждение о дублях источников
  collector.py               оркестратор GisCollector: specs → стратегии → фильтры → Feature
  feature_factory.py         единственное место создания Feature (нормализация + _meta)
  projection.py              spec.attributes → properties (+ приведение типов)
  filters.py                 фильтры результата (точка в полигоне области)
  auth.py                    auth: none | bearer (токен из Settings или переменной окружения)
  jsonpath.py                мини-JSONPath без зависимостей ($, .a, [*], [n], [-1])
  concurrency.py             parallel_map с бюджетом ошибок
  http/                      HttpClient (Protocol) + HttpxClient: ретраи, Retry-After, rate limit
  geo/
    geojson.py               валидация, make_valid, ориентация колец RFC 7946, округление
    tiles.py                 XYZ-математика, перечень тайлов, пересекающих область
    mvt.py                   собственный декодер Mapbox Vector Tile (PBF)
    overpass.py              node/way/relation → GeoJSON (сборка multipolygon)
    cmr.py                   footprints NASA CMR → GeoJSON
    extractors.py            геометрия обычного JSON по spec.geometry
    boundary.py              граница области: файл → Nominatim (R71249) → BBOX
  strategies/
    base.py                  CollectionStrategy (ABC), RunContext, CollectionTask
    json_api.py              JSON REST: ecozagroza, CMR (page_number)
    overpass.py              Overpass POST, ожидание слота, ретраи "runtime error"
    tiles.py                 MVT: перебор тайлов, дедупликация/склейка участков
    dream.py                 DREAM: cursor-индекс + детали + клиентский фильтр КАТОТТГ
    templated.py             {cadnum}-lookup (обогащение)
    skipped.py               styles.json и detail-endpoint-ы (используются другими стратегиями)
    registry.py              порядок стратегий; новый тип источника = новый класс + register()
  cli.py / __main__.py       gis-collector plan|collect
tests/                       54 теста (fake-«интернет» в tests/world.py, MVT-энкодер для тестов)
```

## Как это работает

Спецификации не идеально однородны, поэтому ~70% логики декларативно (URL, параметры, контейнер,
атрибуты, пагинация), а нестандартные случаи вынесены в стратегии. Стратегия выбирается по
форме endpoint-а (первая, чей `supports()` вернул `True`):

| Endpoint | Стратегия |
|---|---|
| `access_class: public_metadata` | `metadata` (не источник; из `styles.json` читаются min/max zoom) |
| `role: index` | `cursor_index_detail` (DREAM) |
| `role: detail` | `consumed_detail` (собирается через index) |
| URL с `{z}/{x}/{y}` | `mvt_tiles` |
| POST + `data` + `/interpreter` | `overpass` |
| URL с другими `{placeholder}` | `templated_lookup` |
| остальное (`pagination: none/page_number`) | `json_api` |

### Формат результата

* `geometry` — GeoJSON в WGS84 (lon, lat), кольца по RFC 7946, 7 знаков после запятой; `None`, если
  у источника нет координат (большинство записей DREAM).
* `properties` — атрибуты из `spec.attributes` (ключ = имя/путь из спецификации; отсутствующие
  атрибуты опускаются) + `properties["_meta"]` (endpoint, layer, feature_id, fetched_at, …).
  Отключается `--no-meta` / `Settings(include_meta=False)`.
* Атрибуты, ушедшие в геометрию (`lat/lon`, `polygons`, `$.geometry`, …), в properties не дублируются.

## Особенности источников (важно)

* **Overpass**: свой `User-Agent` из спецификации, заголовок `Accept` не отправляется (иначе 406),
  перед запросом проверяется `/api/status` (ждём свободный слот), 429/504 — ретраи с `Retry-After`,
  `remark: runtime error` в ответе 200 — тоже ретрай. Безтеговые «скелетные» элементы
  (`>; out skel`) отбрасываются, дубли `(type, id)` схлопываются.
* **`osm_chernihiv` и `overpass` запрашивают один и тот же endpoint** — при запуске обоих будут
  дубли с разными `source_id`. Загрузчик пишет предупреждение; лишний источник отключается `--exclude`.
* **Кадастр (MVT)**: тайлы выбираются по пересечению с настоящей границей области (Nominatim `R71249`;
  `--boundary-file` для офлайна; запасной вариант — BBOX с предупреждением). Зум по слою:
  `Settings.tile_zoom_by_layer` (участки/продажи z11, аукционы z8), ограничивается min/max zoom из
  `styles.json`. Участки, разрезанные границами тайлов, склеиваются по `cadnum`; фильтр по
  representative point внутри области. REST `{cadnum}` — обогащение: `--enrich-limit N` или
  `--template-values file.json`.
* **DREAM**: индекс по курсору `from=<external.updated>` (1000/страница), детали параллельно,
  фильтр области на клиенте по КАТОТТГ-префиксу `UA74`. Всего ~45 тыс. запросов на полный прогон —
  для отладки `--dream-limit N`. Идеи без координат наследуют геометрию связанного объекта.
* **ЭкоЗагроза**: собираются оба слоя (`radiation` + `air_pollution`), затем точный фильтр
  point-in-polygon по границе области (BBOX захватывает соседей и Беларусь).
* **Earthdata CMR**: собираются MOD14 и VNP14 (по `second_sample_params`); `expected_count` трактуется
  как размер выборки «последних N гранул». CMR отдаёт полигоны строками в порядке **lat lon** —
  декодер переводит в lon/lat. Токен (`EARTHDATA_TOKEN` или `GIS_COLLECTOR_TOKEN_EARTHDATA_CHERNIHIV`)
  нужен только для скачивания файлов; поиск метаданных работает без него.

`second_sample_params` в одних спецификациях — второй реальный набор данных (ecozagroza, earthdata),
в других — тестовый запрос (overpass, kadastr). Какие источники собирать с вариантами, задаёт
`Settings.param_variant_sources`.

## Расширение

Новый тип источника: класс, наследующий `CollectionStrategy` (`supports`, `collect`), и
`registry.register(MyStrategy())` — существующий код не меняется. HTTP, границы, авторизация
подменяются через конструктор `GisCollector(http=..., boundary=..., auth=..., registry=...)`.

## Разработка

```bash
pytest                    # 54 теста, сеть не нужна
mypy                      # strict
ruff check src tests
```

## Известные ограничения

* Проверено только на моделях ответов (tests/world.py) — реальные API в среде разработки
  недоступны. Перед продом выполните `gis-collector plan` и дымовой прогон по каждому источнику
  (`--source X --dream-limit 20`).
* Если Overpass-запрос превышает `timeout`, автоматического дробления запроса по bbox нет —
  нужен либо больший `timeout`, либо локальный Overpass.
* Результат каждого endpoint-а атомарен: при сбое посреди endpoint-а его частичные данные
  отбрасываются, ошибка попадает в `CollectionReport`.
* Весь результат хранится в памяти (для области ~ сотни тысяч объектов это нормально; для больших
  объёмов добавьте потоковый `sink` поверх `strategy.collect()`).
