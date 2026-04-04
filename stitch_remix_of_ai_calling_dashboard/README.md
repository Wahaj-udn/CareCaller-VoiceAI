# CareCaller Admin Dashboard

Flask-based admin dashboard for AI calling operations. The app provides a multi-page web UI for monitoring calls, viewing transcript summaries directly in the dashboard, uploading call datasets, and exposing upload management APIs.

## What This Project Includes

- Dashboard analytics page with call KPIs, insights, and charts
- Live call monitoring mock interface
- Transcript analytics section embedded in the dashboard (outcome chart + recent rows)
- Data import UI (drag-drop and click upload)
- Backend upload APIs for upload/list/download/delete
- Health check endpoint for service status

## Tech Stack

- Python 3
- Flask
- Flask-CORS
- Jinja2 templates
- TailwindCSS (CDN)
- Chart.js (CDN)

## Project Structure

```text
stitch_remix_of_ai_calling_dashboard/
|- app.py
|- requirements.txt
|- README.md
|- templates/
|  |- base.html
|  |- dashboard.html
|  |- live-calls.html
|  |- csv-data.html
|- uploads/
|  |- upload_log.json
|  |- <timestamped uploaded files>
|- static/  (currently empty)
```

## How The App Works

1. UI routes render Jinja templates from `templates/`.
2. Upload requests are sent to `POST /api/upload`.
3. Uploaded files are saved in `uploads/` with a timestamp prefix.
4. Upload events are written to `uploads/upload_log.json`.
5. Dashboard parses the newest CSV/JSON file from `uploads/` and computes both KPI and transcript analytics.
6. If no upload exists, the app renders safe fallback states (`n/a`, empty charts, no rows).

## Supported Data Inputs

- Upload validation accepts: `.csv`, `.xlsx`, `.json`
- Max upload size: `50MB`
- Analytics parsing currently reads CSV and JSON for dashboard/transcript metrics
- JSON supports flexible structures:
    - array payloads
    - object payloads
    - nested list keys like `transcripts`, `records`, `data`, `results`, `rows`, `items`

## Core Pages

- `/` or `/dashboard`: KPI cards, insights, outcome/direction/duration charts, and transcript analytics (outcome chart + last 5 transcripts)
- `/live-calls`: active call control and conversation-style UI
- `/csv-data`: upload and validation UI for campaign data
- `/transcripts`: legacy route that redirects to `/dashboard`

## API Endpoints

### `POST /api/upload`
Uploads one file via `multipart/form-data` field `file`.

Example success response:

```json
{
    "success": true,
    "message": "File uploaded successfully",
    "fileName": "result.json",
    "filePath": ".../uploads/20260404_123142_result.json",
    "uniqueFileName": "20260404_123142_result.json",
    "fileSize": 12345,
    "uploadTime": "2026-04-04T12:31:42.000000"
}
```

### `GET /api/uploads`
Lists uploaded files (excluding `upload_log.json`).

### `GET /api/uploads/<filename>`
Downloads a previously uploaded file.

### `DELETE /api/uploads/<filename>`
Deletes a file from `uploads/`.

### `GET /api/health`
Returns service status and upload folder info.

## Setup And Run

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Start the app:

```bash
python app.py
```

3. Open in browser:

```text
http://localhost:8000
```

## Development Notes

- Upload logs are server-side in `uploads/upload_log.json`.
- The CSV upload page also keeps lightweight browser-side history in `localStorage` (`uploadHistory`) as a fallback.
- CORS is enabled in the Flask app.
- Default error handling:
    - API 404 -> JSON error response
    - Page 404 -> dashboard render with 404 status

## Troubleshooting

- Upload fails with size error:
    - Reduce file size below 50MB, or change `MAX_FILE_SIZE` in `app.py`.

- API not reachable from UI:
    - Ensure `python app.py` is running on port 8000.

- No analytics visible:
    - Upload a valid CSV/JSON file and refresh the dashboard page.

- JSON parse errors:
    - Confirm valid JSON syntax and that the payload is an object or array.
