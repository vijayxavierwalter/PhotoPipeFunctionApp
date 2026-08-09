"""
PhotoPipe Function App — Event-Driven Image Processing
CST8917 Lab 4 | Winter 2026

This Function App contains:
- process-image: Event Grid trigger that processes uploaded images and writes metadata
- audit-log: Event Grid trigger that logs all upload events to Table Storage
- get-results: HTTP trigger that returns all processed image metadata
- get-audit-log: HTTP trigger that returns all audit log entries
- health: HTTP trigger for deployment verification
"""

import azure.functions as func
import json
import logging
import os
from datetime import datetime, timezone

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ---------------------------------------------------------------------------
# Helper: get storage clients
# ---------------------------------------------------------------------------

def get_blob_service_client():
    """Create a BlobServiceClient from the STORAGE_CONNECTION_STRING setting."""
    from azure.storage.blob import BlobServiceClient
    conn_str = os.environ["STORAGE_CONNECTION_STRING"]
    return BlobServiceClient.from_connection_string(conn_str)


def get_table_client():
    """Create a TableClient for the processinglog table."""
    from azure.data.tables import TableServiceClient
    conn_str = os.environ["STORAGE_CONNECTION_STRING"]
    service = TableServiceClient.from_connection_string(conn_str)
    # Creates the table if it doesn't already exist
    table_client = service.create_table_if_not_exists("processinglog")
    return table_client


# ---------------------------------------------------------------------------
# Function 1: process-image (Event Grid trigger)
# ---------------------------------------------------------------------------
# Receives a BlobCreated event, reads image metadata, and writes a JSON
# result file to the image-results container.
# ---------------------------------------------------------------------------

@app.function_name(name="process-image")
@app.event_grid_trigger(arg_name="event")
def process_image(event: func.EventGridEvent):
    logging.info("process-image triggered by Event Grid event: %s", event.id)

    # Extract event data
    event_data = event.get_json()
    blob_url = event_data.get("url", "")
    content_type = event_data.get("contentType", "unknown")
    content_length = event_data.get("contentLength", 0)

    # Parse blob name from the URL
    # URL format: https://<account>.blob.core.windows.net/<container>/<blob-name>
    blob_name = blob_url.split("/image-uploads/")[-1] if "/image-uploads/" in blob_url else "unknown"

    logging.info("Processing image: %s (type: %s, size: %d bytes)", blob_name, content_type, content_length)

    # Generate image metadata
    # In a real application, you would download the blob and use a library like
    # Pillow to extract actual dimensions, generate a real thumbnail, detect
    # dominant colors, etc. For this lab, we simulate some metadata fields.
    now = datetime.now(timezone.utc).isoformat()
    metadata = {
        "originalFileName": blob_name,
        "originalUrl": blob_url,
        "contentType": content_type,
        "fileSizeBytes": content_length,
        "fileSizeFormatted": format_file_size(content_length),
        "processedAt": now,
        "eventId": event.id,
        "eventType": event.event_type,
        "thumbnail": {
            "status": "reference-generated",
            "path": f"thumbnails/thumb_{blob_name}",
            "note": "In production, a real thumbnail would be generated here using Pillow or an Azure Computer Vision API call."
        },
        "imageAnalysis": {
            "width": 1920,
            "height": 1080,
            "note": "Simulated dimensions — real analysis would use Pillow or Azure Computer Vision"
        },
        "status": "processed"
    }

    # Write metadata JSON to the image-results container
    result_blob_name = f"{blob_name}.json"
    try:
        blob_service = get_blob_service_client()
        results_container = blob_service.get_container_client("image-results")
        results_container.upload_blob(
            name=result_blob_name,
            data=json.dumps(metadata, indent=2),
            content_type="application/json",
            overwrite=True
        )
        logging.info("Metadata written to image-results/%s", result_blob_name)
    except Exception as e:
        logging.error("Failed to write metadata: %s", str(e))
        raise


def format_file_size(size_bytes: int) -> str:
    """Format a byte count into a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Function 2: audit-log (Event Grid trigger)
# ---------------------------------------------------------------------------
# Receives a BlobCreated event and writes an audit entry to Table Storage.
# This function fires for ALL blob uploads (no suffix filter), providing a
# complete record of every file uploaded to the image-uploads container.
# ---------------------------------------------------------------------------

@app.function_name(name="audit-log")
@app.event_grid_trigger(arg_name="event")
def audit_log(event: func.EventGridEvent):
    logging.info("audit-log triggered by Event Grid event: %s", event.id)

    event_data = event.get_json()
    blob_url = event_data.get("url", "")
    content_type = event_data.get("contentType", "unknown")
    content_length = event_data.get("contentLength", 0)
    blob_name = blob_url.split("/image-uploads/")[-1] if "/image-uploads/" in blob_url else "unknown"

    now = datetime.now(timezone.utc)

    # Table Storage entity
    # PartitionKey: date string (e.g., "2026-04-07") for easy date-range queries
    # RowKey: event ID (guaranteed unique by Event Grid)
    entity = {
        "PartitionKey": now.strftime("%Y-%m-%d"),
        "RowKey": event.id,
        "BlobName": blob_name,
        "BlobUrl": blob_url,
        "ContentType": content_type,
        "ContentLength": content_length,
        "EventType": event.event_type,
        "EventTime": event.event_time.isoformat() if event.event_time else now.isoformat(),
        "ProcessedAt": now.isoformat(),
        "Status": "logged"
    }

    try:
        table_client = get_table_client()
        table_client.upsert_entity(entity)
        logging.info("Audit log entry written for %s", blob_name)
    except Exception as e:
        logging.error("Failed to write audit log: %s", str(e))
        raise


# ---------------------------------------------------------------------------
# Function 3: get-results (HTTP trigger)
# ---------------------------------------------------------------------------
# Returns all processed image metadata from the image-results container.
# The web client calls this endpoint to display processing results.
# ---------------------------------------------------------------------------

@app.function_name(name="get-results")
@app.route(route="get-results", methods=["GET"])
def get_results(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("get-results called")

    try:
        blob_service = get_blob_service_client()
        results_container = blob_service.get_container_client("image-results")
        results = []

        for blob in results_container.list_blobs():
            if blob.name.endswith(".json"):
                blob_client = results_container.get_blob_client(blob.name)
                data = blob_client.download_blob().readall()
                results.append(json.loads(data))

        # Sort by processedAt descending (newest first)
        results.sort(key=lambda x: x.get("processedAt", ""), reverse=True)

        return func.HttpResponse(
            body=json.dumps(results, indent=2),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        logging.error("Failed to get results: %s", str(e))
        return func.HttpResponse(
            body=json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )


# ---------------------------------------------------------------------------
# Function 4: get-audit-log (HTTP trigger)
# ---------------------------------------------------------------------------
# Returns all audit log entries from Table Storage.
# The web client calls this endpoint to display the audit trail.
# ---------------------------------------------------------------------------

@app.function_name(name="get-audit-log")
@app.route(route="get-audit-log", methods=["GET"])
def get_audit_log(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("get-audit-log called")

    try:
        table_client = get_table_client()
        entities = list(table_client.list_entities())

        # Convert table entities to plain dicts and sort by ProcessedAt descending
        results = []
        for entity in entities:
            results.append({
                "partitionKey": entity["PartitionKey"],
                "rowKey": entity["RowKey"],
                "blobName": entity.get("BlobName", ""),
                "blobUrl": entity.get("BlobUrl", ""),
                "contentType": entity.get("ContentType", ""),
                "contentLength": entity.get("ContentLength", 0),
                "eventType": entity.get("EventType", ""),
                "eventTime": entity.get("EventTime", ""),
                "processedAt": entity.get("ProcessedAt", ""),
                "status": entity.get("Status", "")
            })

        results.sort(key=lambda x: x.get("processedAt", ""), reverse=True)

        return func.HttpResponse(
            body=json.dumps(results, indent=2),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        logging.error("Failed to get audit log: %s", str(e))
        return func.HttpResponse(
            body=json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )


# ---------------------------------------------------------------------------
# Function 5: health (HTTP trigger)
# ---------------------------------------------------------------------------

@app.function_name(name="health")
@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps({
            "status": "healthy",
            "service": "PhotoPipe Function App"
        }),
        mimetype="application/json",
        status_code=200
    )