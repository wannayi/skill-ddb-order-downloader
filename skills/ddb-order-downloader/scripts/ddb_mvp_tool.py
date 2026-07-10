from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from collections import namedtuple
from pathlib import Path


API_SEARCH_URL = "https://api.deutsche-digitale-bibliothek.de/search/index/newspaper-issues/newspaper-search"
DEFAULT_ROWS_PER_REQUEST = 500
USER_AGENT = "Mozilla/5.0 compatible; DDB MVP downloader for personal research"
ORDER_FIELDS = ["order_id", "customer", "ddb_url", "output_name", "filter_mode", "filter_value", "status", "limit"]
STATUS_FIELDS = [
    "order_id",
    "customer",
    "output_name",
    "input_status",
    "result_status",
    "num_saved",
    "zip_path",
    "output_root",
    "error",
]

ParsedSearch = namedtuple("ParsedSearch", ["source_url", "query"])


def parse_ddb_newspaper_url(url: str) -> ParsedSearch:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if not host.endswith("deutsche-digitale-bibliothek.de"):
        raise ValueError("URL host must be deutsche-digitale-bibliothek.de")
    if parsed.path.rstrip("/") != "/search/newspaper":
        raise ValueError("URL path must be /search/newspaper")
    params = urllib.parse.parse_qs(parsed.query)
    query_values = params.get("query")
    if not query_values or not query_values[0].strip():
        raise ValueError("DDB newspaper URL must contain a non-empty query parameter")
    return ParsedSearch(source_url=url, query=query_values[0].strip())


def slugify(value: str, max_length: int = 64) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower()
    ascii_value = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return (ascii_value or "ddb-order")[:max_length].strip("-")


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def build_filter(mode: str, value: str | None):
    mode = (mode or "none").lower()
    if mode == "none":
        return lambda text: True
    if not value:
        raise ValueError(f"--filter-value is required when --filter-mode is {mode}")
    if mode == "exact":
        needle = normalize_spaces(value).casefold()

        def exact_matcher(text: str) -> bool:
            return needle in normalize_spaces(text).casefold()

        return exact_matcher
    if mode == "regex":
        pattern = re.compile(value, flags=re.IGNORECASE)
        return lambda text: bool(pattern.search(text))
    raise ValueError("--filter-mode must be one of: none, exact, regex")


def filter_description(mode: str, value: str | None) -> str:
    mode = (mode or "none").lower()
    if mode == "none":
        return "none"
    return f"{mode}: {value}"


def clean_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def date_only(value: str) -> str:
    return value[:10] if value else "unknown-date"


def make_filename(index: int, doc: dict, suffix: str) -> str:
    date = date_only(clean_text(doc.get("publication_date")))
    title = slugify(clean_text(doc.get("paper_title")), max_length=42)
    page = clean_text(doc.get("pagenumber")) or "unknown"
    doc_id = slugify(clean_text(doc.get("id")), max_length=12)
    return f"{index:04d}_{date}_{title}_p{page}_{doc_id}.{suffix}"


def make_snippet(text: str, matcher=None, radius: int = 420) -> str:
    if not text:
        return ""
    fallback_patterns = [
        r"chinesisch\w+\s+Student\w+",
        r"chinesisch\w+\s+Studierend\w+",
        r"chinesisch\w+",
        r"Student\w+",
        r"Studierend\w+",
        r"Deutsch\w*",
    ]
    for pattern in fallback_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            start = max(0, match.start() - radius)
            end = min(len(text), match.end() + radius)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            if start > 0:
                snippet = "... " + snippet
            if end < len(text):
                snippet += " ..."
            return snippet
    return re.sub(r"\s+", " ", text[: radius * 2]).strip()


def build_api_url(query: str, start: int, rows: int) -> str:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "rows": str(rows),
            "start": str(start),
            "wt": "json",
        }
    )
    return f"{API_SEARCH_URL}?{params}"


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str, timeout: int = 60) -> dict:
    return json.loads(fetch_bytes(url, timeout=timeout).decode("utf-8"))


def fetch_all_docs(query: str, rows_per_request: int, limit: int | None, timeout: int) -> tuple[list[dict], int, list[str]]:
    rows = max(1, min(rows_per_request, DEFAULT_ROWS_PER_REQUEST))
    first_url = build_api_url(query, 0, rows)
    payload = fetch_json(first_url, timeout=timeout)
    total = int(payload.get("response", {}).get("numFound") or 0)
    docs = list(payload.get("response", {}).get("docs", []))
    api_urls = [first_url]

    target = min(total, limit) if limit else total
    while len(docs) < target:
        url = build_api_url(query, len(docs), rows)
        page = fetch_json(url, timeout=timeout)
        page_docs = list(page.get("response", {}).get("docs", []))
        api_urls.append(url)
        if not page_docs:
            break
        docs.extend(page_docs)

    return docs[:target], total, api_urls


def make_output_paths(base_dir: Path, slug: str) -> tuple[Path, Path]:
    output_root = base_dir / slug
    zip_path = base_dir / f"{slug}.zip"
    return output_root, zip_path


def write_text_file(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def write_result_files(
    output_root: Path,
    docs: list[dict],
    source_url: str,
    query: str,
    filter_description: str,
    api_urls: list[str],
    num_found: int,
) -> list[dict]:
    output_root.mkdir(parents=True, exist_ok=True)
    texts_dir = output_root / "texts"
    texts_dir.mkdir(exist_ok=False)

    fetched_at = dt.datetime.now(dt.UTC).isoformat()
    rows = []
    snippets = []
    combined_parts = [
        f"# DDB newspaper results: {query}\n",
        f"Source search URL: {source_url}\n",
        f"Query: {query}\n",
        f"Filter: {filter_description}\n",
        f"Fetched at UTC: {fetched_at}\n",
        f"API records found: {num_found}\n",
        f"Records saved: {len(docs)}\n",
        "Note: DDB returns page-level OCR, not manually segmented article clippings.\n",
    ]

    for index, doc in enumerate(docs, start=1):
        text = clean_text(doc.get("plainpagefulltext"))
        txt_name = make_filename(index, doc, "txt")
        text_file = f"texts/{txt_name}"
        write_text_file(output_root / text_file, text)

        thumbnail_uuid = clean_text(doc.get("thumbnail"))
        thumbnail_url = (
            f"https://api.deutsche-digitale-bibliothek.de/binary/{thumbnail_uuid}"
            if thumbnail_uuid
            else ""
        )
        snippet = make_snippet(text)
        row = {
            "index": index,
            "id": clean_text(doc.get("id")),
            "publication_date": clean_text(doc.get("publication_date")),
            "paper_title": clean_text(doc.get("paper_title")),
            "page_number": clean_text(doc.get("pagenumber")),
            "provider": clean_text(doc.get("provider")),
            "provider_ddb_id": clean_text(doc.get("provider_ddb_id")),
            "zdb_id": clean_text(doc.get("zdb_id")),
            "place_of_distribution": clean_text(doc.get("place_of_distribution")),
            "language": clean_text(doc.get("language")),
            "text_file": text_file,
            "alto_xml_url": clean_text(doc.get("preview_reference")),
            "thumbnail_url": thumbnail_url,
            "snippet": snippet,
        }
        rows.append(row)
        snippets.append(
            {
                "index": index,
                "publication_date": row["publication_date"],
                "paper_title": row["paper_title"],
                "page_number": row["page_number"],
                "text_file": row["text_file"],
                "snippet": snippet,
            }
        )
        combined_parts.extend(
            [
                "\n---\n",
                f"\n## {index:04d}. {row['paper_title']}\n",
                f"- Date: {date_only(row['publication_date'])}\n",
                f"- Page: {row['page_number']}\n",
                f"- Provider: {row['provider']}\n",
                f"- DDB result ID: `{row['id']}`\n",
                f"- ALTO XML URL: {row['alto_xml_url']}\n",
                f"\nSnippet: {snippet}\n",
                "\nFull OCR text:\n\n```text\n",
                text,
                "\n```\n",
            ]
        )

    with (output_root / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["index"])
        writer.writeheader()
        writer.writerows(rows)

    with (output_root / "snippets.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(snippets[0].keys()) if snippets else ["index"])
        writer.writeheader()
        writer.writerows(snippets)

    manifest = {
        "source_search_url": source_url,
        "api_search_urls": api_urls,
        "query": query,
        "filter": filter_description,
        "fetched_at_utc": fetched_at,
        "num_found_by_api": num_found,
        "num_saved": len(docs),
        "ddb_note": "DDB newspaper search exposes page-level OCR records, not manually segmented article clippings.",
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "results.json").write_text(
        json.dumps({"manifest": manifest, "docs": docs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_text_file(output_root / "all_results.md", "\n".join(combined_parts))
    write_text_file(
        output_root / "README.md",
        "\n".join(
            [
                "# DDB newspaper download",
                "",
                f"Source search page: {source_url}",
                "",
                f"Query: `{query}`",
                "",
                f"Filter: `{filter_description}`",
                "",
                f"Downloaded records: {len(docs)} of {num_found}",
                "",
                "The DDB newspaper endpoint returns page-level OCR records. The saved text files are matching newspaper pages, not manually segmented article clippings.",
                "",
                "Files:",
                "",
                "- `metadata.csv`: one row per result, with source URLs and local file names.",
                "- `snippets.csv`: shorter context snippets around likely search terms.",
                "- `texts/`: UTF-8 OCR text for each result page.",
                "- `results.json`: raw API result data plus manifest.",
                "- `all_results.md`: combined Markdown version with metadata, snippets, and full OCR text.",
            ]
        ),
    )
    return rows


def zip_output(output_root: Path, zip_path: Path) -> None:
    if zip_path.exists():
        raise FileExistsError(f"Zip file already exists: {zip_path}")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_root.rglob("*")):
            archive.write(path, path.relative_to(output_root.parent))


def verify_output(output_root: Path, zip_path: Path | None, expected_rows: int) -> dict:
    metadata_path = output_root / "metadata.csv"
    with metadata_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    missing = [row["text_file"] for row in rows if not (output_root / row["text_file"]).exists()]
    text_count = len(list((output_root / "texts").glob("*.txt")))
    zip_entries = None
    if zip_path is not None:
        with zipfile.ZipFile(zip_path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"Zip integrity check failed at {bad}")
            zip_entries = len(archive.namelist())
    return {
        "metadata_rows": len(rows),
        "text_files": text_count,
        "missing_text_files": len(missing),
        "expected_rows": expected_rows,
        "zip_entries": zip_entries,
    }


def read_orders_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    normalized_rows = []
    for row in rows:
        normalized = {field: clean_text(row.get(field)).strip() for field in ORDER_FIELDS}
        normalized["status"] = normalized["status"] or "pending"
        normalized["filter_mode"] = normalized["filter_mode"] or "none"
        normalized_rows.append(normalized)
    return normalized_rows


def order_to_args(order: dict, output_base: Path) -> argparse.Namespace:
    name = order.get("output_name") or order.get("order_id") or None
    limit_text = clean_text(order.get("limit")).strip()
    return argparse.Namespace(
        url=order["ddb_url"],
        output_base=str(output_base),
        name=name,
        filter_mode=order.get("filter_mode") or "none",
        filter_value=order.get("filter_value") or None,
        limit=int(limit_text) if limit_text else None,
        rows_per_request=DEFAULT_ROWS_PER_REQUEST,
        timeout=60,
        no_zip=False,
    )


def make_customer_reply(order: dict, result: dict) -> str:
    customer = order.get("customer") or "您好"
    order_id = order.get("order_id") or order.get("output_name") or ""
    zip_path = result.get("zip_path") or ""
    num_saved = result.get("num_saved")
    return "\n".join(
        [
            f"【{order_id}】{customer}，资料已整理完成。",
            f"本次保存 OCR 文本页数：{num_saved}",
            f"交付压缩包：{zip_path}",
            "说明：DDB 返回的是命中报纸页 OCR，不是人工切分后的单篇文章；OCR 可能存在识别错误。",
            "",
        ]
    )


def process_orders(
    order_rows: list[dict],
    output_base: Path,
    status_path: Path,
    replies_path: Path,
    runner=None,
    continue_on_error: bool = True,
) -> dict:
    if runner is None:
        runner = run_order
    output_base.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    replies_path.parent.mkdir(parents=True, exist_ok=True)

    status_rows = []
    replies = []
    processed = 0
    skipped = 0
    failed = 0

    for order in order_rows:
        input_status = (order.get("status") or "pending").strip().lower()
        base_status = {
            "order_id": order.get("order_id", ""),
            "customer": order.get("customer", ""),
            "output_name": order.get("output_name", ""),
            "input_status": input_status,
            "result_status": "",
            "num_saved": "",
            "zip_path": "",
            "output_root": "",
            "error": "",
        }
        if input_status not in {"", "pending", "todo", "待处理"}:
            base_status["result_status"] = "skipped"
            status_rows.append(base_status)
            skipped += 1
            continue
        try:
            result = runner(order_to_args(order, output_base))
            base_status.update(
                {
                    "result_status": "done",
                    "num_saved": clean_text(result.get("num_saved")),
                    "zip_path": clean_text(result.get("zip_path")),
                    "output_root": clean_text(result.get("output_root")),
                }
            )
            replies.append(make_customer_reply(order, result))
            processed += 1
        except Exception as exc:
            base_status["result_status"] = "failed"
            base_status["error"] = str(exc)
            failed += 1
            if not continue_on_error:
                status_rows.append(base_status)
                break
        status_rows.append(base_status)

    with status_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(status_rows)
    replies_path.write_text("\n".join(replies), encoding="utf-8", newline="\n")
    return {
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "status_path": str(status_path),
        "replies_path": str(replies_path),
    }


def run_order(args: argparse.Namespace) -> dict:
    parsed = parse_ddb_newspaper_url(args.url)
    matcher = build_filter(args.filter_mode, args.filter_value)
    docs, num_found, api_urls = fetch_all_docs(parsed.query, args.rows_per_request, args.limit, args.timeout)
    filtered_docs = [doc for doc in docs if matcher(clean_text(doc.get("plainpagefulltext")))]

    base_dir = Path(args.output_base).resolve()
    if args.name:
        slug = slugify(args.name, max_length=80)
    else:
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = f"{slugify(parsed.query, max_length=52)}-{timestamp}"
    output_root, zip_path = make_output_paths(base_dir, slug)
    if output_root.exists():
        raise FileExistsError(f"Output directory already exists: {output_root}")

    rows = write_result_files(
        output_root=output_root,
        docs=filtered_docs,
        source_url=parsed.source_url,
        query=parsed.query,
        filter_description=filter_description(args.filter_mode, args.filter_value),
        api_urls=api_urls,
        num_found=num_found,
    )
    final_zip_path = None
    if not args.no_zip:
        zip_output(output_root, zip_path)
        final_zip_path = zip_path
    verification = verify_output(output_root, final_zip_path, expected_rows=len(rows))
    return {
        "output_root": str(output_root),
        "zip_path": str(final_zip_path) if final_zip_path else "",
        "num_found_by_api": num_found,
        "num_fetched_before_filter": len(docs),
        "num_saved": len(rows),
        "verification": verification,
    }


def build_parser() -> argparse.ArgumentParser:
    default_output_base = Path(__file__).resolve().parents[1] / "ddb_mvp_runs"
    parser = argparse.ArgumentParser(description="Download DDB newspaper OCR search results into a delivery folder.")
    subparsers = parser.add_subparsers(dest="command")

    single = subparsers.add_parser("single", help="Process one DDB URL")
    single.add_argument("url", help="DDB newspaper search URL")
    single.add_argument("--output-base", default=str(default_output_base), help="Directory that will contain order folders")
    single.add_argument("--name", help="Optional output folder name; defaults to query plus timestamp")
    single.add_argument("--filter-mode", choices=["none", "exact", "regex"], default="none")
    single.add_argument("--filter-value", help="Exact phrase or regex used when --filter-mode is not none")
    single.add_argument("--limit", type=int, help="Maximum number of API records to fetch before filtering")
    single.add_argument("--rows-per-request", type=int, default=DEFAULT_ROWS_PER_REQUEST)
    single.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds")
    single.add_argument("--no-zip", action="store_true", help="Skip zip generation")

    batch = subparsers.add_parser("batch", help="Process pending rows from an orders CSV")
    batch.add_argument("orders_csv", help="CSV with order_id, customer, ddb_url, output_name, filter_mode, filter_value, status, limit")
    batch.add_argument("--output-base", default=str(default_output_base), help="Directory that will contain order folders")
    batch.add_argument("--status-path", help="CSV path for processing status")
    batch.add_argument("--replies-path", help="Text path for customer reply templates")
    batch.add_argument("--stop-on-error", action="store_true", help="Stop batch after the first failed order")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    effective_argv = list(argv if argv is not None else sys.argv[1:])
    if effective_argv and effective_argv[0] not in {"single", "batch"} and not effective_argv[0].startswith("-"):
        effective_argv = ["single", *effective_argv]
    args = parser.parse_args(effective_argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "batch":
            orders_csv = Path(args.orders_csv).resolve()
            output_base = Path(args.output_base).resolve()
            status_path = Path(args.status_path).resolve() if args.status_path else orders_csv.with_name("order_status.csv")
            replies_path = Path(args.replies_path).resolve() if args.replies_path else orders_csv.with_name("customer_replies.txt")
            result = process_orders(
                order_rows=read_orders_csv(orders_csv),
                output_base=output_base,
                status_path=status_path,
                replies_path=replies_path,
                continue_on_error=not args.stop_on_error,
            )
        else:
            result = run_order(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
