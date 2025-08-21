# web/main.py
import asyncio
import json
import os
import zipfile
from io import BytesIO
from urllib.parse import quote
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Form, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response
from pathlib import Path
import sys
from typing import List
from creart import add_creator

# Add the project root to the Python path
sys.path.append(str(Path(__file__).parent.parent))

# Add the necessary creators
from src.logger import LoggerCreator
add_creator(LoggerCreator)
from src.config import ConfigCreator, Config
add_creator(ConfigCreator)
from src.api import APICreator, WebAPI
add_creator(APICreator)
from src.grpc.manager import WMCreator, WrapperManager
add_creator(WMCreator)
from src.measurer import MeasurerCreator, SpeedMeasurer
add_creator(MeasurerCreator)

from src.rip import rip_song, rip_album, rip_artist, rip_playlist, on_decrypt_success, on_decrypt_failed, adam_id_task_mapping
from src.url import AppleMusicURL, URLType
from src.flags import Flags
from src.utils import safely_create_task, run_sync
from creart import it

app = FastAPI()
DOWNLOADS_DIR = Path("downloads")
log_history = []

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        log_history.append(message)
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

async def send_progress_updates():
    while True:
        await asyncio.sleep(1)
        if manager.active_connections:
            download_speed = it(SpeedMeasurer).download_speed()
            decrypt_speed = it(SpeedMeasurer).decrypt_speed()
            
            tasks = []
            refresh_needed = False
            for task_id, task in list(adam_id_task_mapping.items()):
                # Safely access metadata and its attributes
                metadata = getattr(task, 'metadata', None)
                task_status = task.status.value
                tasks.append({
                    "id": task_id,
                    "name": getattr(metadata, 'title', 'Loading...'),
                    "artist": getattr(metadata, 'artist', 'N/A'),
                    "status": task_status
                })
                if task_status == "DONE":
                    refresh_needed = True


            progress = {
                "type": "progress",
                "download_speed": download_speed,
                "decrypt_speed": decrypt_speed,
                "tasks": tasks
            }
            await manager.broadcast(json.dumps(progress))
            if refresh_needed:
                await manager.broadcast(json.dumps({"type": "download_complete"}))


@app.on_event("startup")
async def startup_event():
    # Ensure downloads directory exists
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    
    # Initialize all the necessary components
    config = it(Config)
    web_api = it(WebAPI)
    wrapper_manager = it(WrapperManager)
    
    # Run synchronous init methods if any (WebAPI.init is a placeholder)
    await run_sync(web_api.init)
    
    # Initialize the WrapperManager gRPC client
    await wrapper_manager.init(config.instance.url, config.instance.secure)
    
    # Start the background decryption task handler
    safely_create_task(wrapper_manager.decrypt_init(on_success=on_decrypt_success, on_failure=on_decrypt_failed))
    
    # Start the progress update task
    safely_create_task(send_progress_updates()) # Using safely_create_task to handle exceptions


@app.get("/")
async def get():
    return HTMLResponse(Path("web/static/index.html").read_text())

def get_file_tree(path, sort_by='name', sort_order='asc', is_top_level=True):
    items = [item for item in os.listdir(path) if not item.startswith('.')]
    
    if is_top_level:
        # Separate folders and files at the top level
        folders = [item for item in items if (path / item).is_dir()]
        files = [item for item in items if not (path / item).is_dir()]

        # Sort folders based on user's choice
        reverse = sort_order == 'desc'
        if sort_by == 'date':
            folder_key_func = lambda item: (path / item).stat().st_mtime
        else:
            folder_key_func = lambda item: item.lower()
        
        sorted_folders = sorted(folders, key=folder_key_func, reverse=reverse)
        
        # Sort files always by name
        sorted_files = sorted(files, key=lambda item: item.lower())
        
        # Combine them, folders first
        sorted_items = sorted_folders + sorted_files
    else:
        # For subdirectories, always sort by name
        sorted_items = sorted(items, key=lambda item: item.lower())

    tree = []
    for item in sorted_items:
        item_path = path / item
        node = {"name": item}
        if item_path.is_dir():
            node["type"] = "directory"
            # Recursive call is no longer top level
            node["children"] = get_file_tree(item_path, sort_by, sort_order, is_top_level=False)
        else:
            node["type"] = "file"
        tree.append(node)
    return tree

@app.get("/files")
async def list_files(sort_by: str = Query('name'), sort_order: str = Query('asc')):
    if not DOWNLOADS_DIR.exists():
        return {"error": "Downloads directory not found"}, 404
    return get_file_tree(DOWNLOADS_DIR, sort_by, sort_order, is_top_level=True)

@app.get("/download/{file_path:path}")
async def download_file(file_path: str):
    full_path = DOWNLOADS_DIR / file_path
    if full_path.exists() and full_path.is_file():
        return FileResponse(full_path, media_type='application/octet-stream', filename=full_path.name)
    return {"error": "File not found"}, 404
    
@app.post("/download-zip")
async def download_zip(files: str = Form(...), zip_name: str = Form(...)):
    file_list = json.loads(files)
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in file_list:
            full_path = DOWNLOADS_DIR / file_path
            if full_path.exists() and full_path.is_file():
                zip_file.write(full_path, arcname=file_path)

    zip_buffer.seek(0)
    
    zip_size = zip_buffer.getbuffer().nbytes
    encoded_zip_name = quote(f"{zip_name}.zip")

    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_zip_name}",
            "Content-Length": str(zip_size)
        }
    )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    # Send history to the newly connected client
    for msg in log_history:
        await websocket.send_text(msg)
    try:
        while True:
            data = await websocket.receive_text()
            url = AppleMusicURL.parse_url(data)
            if not url:
                await manager.broadcast(json.dumps({"type": "log", "message": "Invalid URL"}))
                continue

            async def log_to_websocket(message: str):
                try:
                    await manager.broadcast(json.dumps({"type": "log", "message": message.strip()}))
                except WebSocketDisconnect:
                    pass

            try:
                if url.type == URLType.Song:
                    safely_create_task(rip_song(url, "alac", Flags(force_save=True), log_callback=log_to_websocket))
                elif url.type == URLType.Album:
                    safely_create_task(rip_album(url, "alac", Flags(force_save=True), log_callback=log_to_websocket))
                elif url.type == URLType.Artist:
                    safely_create_task(rip_artist(url, "alac", Flags(force_save=True), log_callback=log_to_websocket))
                elif url.type == URLType.Playlist:
                    safely_create_task(rip_playlist(url, "alac", Flags(force_save=True), log_callback=log_to_websocket))
            except Exception as e:
                await log_to_websocket(f"An error occurred: {e}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("Client disconnected")