import streamlit as st
import re
import json
import os
import requests
from PIL import Image
import io
import matplotlib.pyplot as plt
from datetime import datetime
import time
import shutil
import tempfile
import pickle
import zipfile
import base64
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload
from dotenv import load_dotenv

load_dotenv()

# Constants
GDRIVE_FOLDER_NAME = "PuertoRicoArchive"
GDRIVE_SESSION_STATE_FILENAME = "/familysearch_session_state.pkl"

def get_google_drive_service():
    """Create and return an authenticated Google Drive service."""
    # Check for credentials in environment variable first (most secure)
    creds_json = os.getenv('GOOGLE_DRIVE_CREDENTIALS')
    
    if creds_json:
        # Load credentials from environment variable
        import json
        from io import StringIO
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
    else:
        # Check for credentials file path in environment variable (second option)
        creds_file = os.getenv('GOOGLE_DRIVE_CREDENTIALS_FILE')
        if not creds_file:
            st.error("Google Drive credentials not found. Set GOOGLE_DRIVE_CREDENTIALS or GOOGLE_DRIVE_CREDENTIALS_FILE environment variable.")
            return None
        
        # Load credentials from file path
        try:
            creds = Credentials.from_service_account_file(creds_file, scopes=['https://www.googleapis.com/auth/drive'])
        except Exception as e:
            st.error(f"Error loading Google Drive credentials: {e}")
            return None
    
    try:
        # Create Drive API client
        service = build('drive', 'v3', credentials=creds)
        # Test connection with a simple request
        service.files().list(pageSize=1).execute()
        return service
    except Exception as e:
        st.error(f"Google Drive connection error: {e}")
        return None
        
# Set page config
st.set_page_config(
    page_title="FamilySearch Image Downloader",
    page_icon="📷",
    layout="wide"
)

def get_or_create_folder(service, folder_name):
    """
    Get the folder ID for the specified folder name, creating it if it doesn't exist.
    """
    # Check if folder already exists
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query).execute()
    items = results.get('files', [])
    
    if items:
        # Folder exists, return its ID
        return items[0]['id']
    else:
        # Create the folder
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        
        folder = service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()
        
        return folder.get('id')

# Functions for session state persistence
def save_session_state():
    """Save session state to Google Drive in the specified folder."""
    drive_service = get_google_drive_service()
    if not drive_service:
        st.warning("Could not connect to Google Drive. Session state will not be saved.")
        return False
    
    state_to_save = {
        'extracted_ids': st.session_state.extracted_ids,
        'download_progress': st.session_state.download_progress,
        'download_started': st.session_state.download_started,
        'current_step': st.session_state.current_step,
        'authorization': st.session_state.authorization,
        'delay_between_downloads': st.session_state.delay_between_downloads
    }
    
    try:
        # Get or create the folder
        folder_id = get_or_create_folder(drive_service, GDRIVE_FOLDER_NAME)
        
        # Serialize the session state to bytes
        with io.BytesIO() as stream:
            pickle.dump(state_to_save, stream)
            stream.seek(0)
            
            # Check if file already exists in the folder
            query = f"name='{GDRIVE_SESSION_STATE_FILENAME}' and '{folder_id}' in parents and trashed=false"
            results = drive_service.files().list(q=query).execute()
            items = results.get('files', [])
            
            media = MediaIoBaseUpload(stream, mimetype='application/octet-stream')
            
            if items:
                # Update existing file
                file_id = items[0]['id']
                drive_service.files().update(
                    fileId=file_id,
                    media_body=media
                ).execute()
            else:
                # Create new file in the specified folder
                file_metadata = {
                    'name': GDRIVE_SESSION_STATE_FILENAME,
                    'parents': [folder_id]  # This puts the file in the specified folder
                }
                drive_service.files().create(
                    body=file_metadata,
                    media_body=media
                ).execute()
        
        return True
    except Exception as e:
        st.warning(f"Error saving session state to Google Drive: {e}")
        import traceback
        st.warning(traceback.format_exc())
        return False
    
def load_session_state():
    """Load session state from Google Drive if it exists in the specified folder."""
    drive_service = get_google_drive_service()
    if not drive_service:
        st.warning("Could not connect to Google Drive. Unable to load previous session.")
        return False
    
    try:
        # Get the folder ID if it exists
        folder_query = f"name='{GDRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        folder_results = drive_service.files().list(q=folder_query).execute()
        folder_items = folder_results.get('files', [])
        
        if not folder_items:
            # Folder doesn't exist
            return False
            
        folder_id = folder_items[0]['id']
        
        # Check if the file exists in the folder
        query = f"name='{GDRIVE_SESSION_STATE_FILENAME}' and '{folder_id}' in parents and trashed=false"
        results = drive_service.files().list(q=query).execute()
        items = results.get('files', [])
        
        if not items:
            # File doesn't exist in the folder
            return False
            
        # Download the file
        file_id = items[0]['id']
        request = drive_service.files().get_media(fileId=file_id)
        
        with io.BytesIO() as stream:
            downloader = MediaIoBaseDownload(stream, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            
            stream.seek(0)
            saved_state = pickle.load(stream)
            
            # Restore saved state to session_state
            for key, value in saved_state.items():
                st.session_state[key] = value
                
            st.success(f"Loaded previous session from Google Drive folder: {GDRIVE_FOLDER_NAME}")
            return True
            
    except Exception as e:
        st.warning(f"Error loading session state from Google Drive: {e}")
        return False    

# Initialize session state variables if they don't exist
if 'initialized' not in st.session_state:
    if load_session_state():
        st.session_state.initialized = True
    else:
        # Initialize with defaults if no saved state
        st.session_state.extracted_ids = []
        st.session_state.download_progress = {
            "completed": [],
            "failed": [],
            "metadata": {
                "town_name": "",
                "date_period": "",
                "letter_code": "",
                "total_ids": 0
            },
            "id_position_map": {},
            "image_data": {}  # Store image binary data for direct download
        }
        st.session_state.download_started = False
        st.session_state.current_step = 1  # 1: Extract IDs, 2: Configure, 3: Download
        st.session_state.authorization = ""
        st.session_state.delay_between_downloads = 0.2
        st.session_state.initialized = True

def load_session_state():
    """Load session state from Google Drive if it exists in the specified folder."""
    drive_service = get_google_drive_service()
    if not drive_service:
        st.warning("Could not connect to Google Drive. Unable to load previous session.")
        return False
    
    try:
        # Get the folder ID if it exists
        folder_query = f"name='{GDRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        folder_results = drive_service.files().list(q=folder_query).execute()
        folder_items = folder_results.get('files', [])
        
        if not folder_items:
            # Folder doesn't exist
            return False
            
        folder_id = folder_items[0]['id']
        
        # Check if the file exists in the folder
        query = f"name='{GDRIVE_SESSION_STATE_FILENAME}' and '{folder_id}' in parents and trashed=false"
        results = drive_service.files().list(q=query).execute()
        items = results.get('files', [])
        
        if not items:
            # File doesn't exist in the folder
            return False
            
        # Download the file
        file_id = items[0]['id']
        request = drive_service.files().get_media(fileId=file_id)
        
        with io.BytesIO() as stream:
            downloader = MediaIoBaseDownload(stream, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            
            stream.seek(0)
            saved_state = pickle.load(stream)
            
            # Restore saved state to session_state
            for key, value in saved_state.items():
                st.session_state[key] = value
                
            st.success(f"Loaded previous session from Google Drive folder: {GDRIVE_FOLDER_NAME}")
            return True
            
    except Exception as e:
        st.warning(f"Error loading session state from Google Drive: {e}")
        return False

def cleanup_google_drive_state():
    """Delete session state file from Google Drive when requested."""
    drive_service = get_google_drive_service()
    if not drive_service:
        st.warning("Could not connect to Google Drive for cleanup.")
        return False
    
    try:
        # Find the folder
        folder_query = f"name='{GDRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        folder_results = drive_service.files().list(q=folder_query).execute()
        folder_items = folder_results.get('files', [])
        
        if not folder_items:
            # Folder doesn't exist
            return True
            
        folder_id = folder_items[0]['id']
        
        # Find the file in the folder
        query = f"name='{GDRIVE_SESSION_STATE_FILENAME}' and '{folder_id}' in parents and trashed=false"
        results = drive_service.files().list(q=query).execute()
        items = results.get('files', [])
        
        if not items:
            # File doesn't exist
            return True
            
        # Delete the file
        file_id = items[0]['id']
        drive_service.files().delete(fileId=file_id).execute()
        return True
    except Exception as e:
        st.warning(f"Error cleaning up Google Drive session state: {e}")
        return False

def extract_ids_from_urls(content):
    """Extract IDs from FamilySearch URLs in content."""
    st.write("Extracting IDs from URLs...")
    
    # Try to parse as JSON first
    try:
        data = json.loads(content)
        # Handle different possible JSON structures
        if isinstance(data, list) and all(isinstance(item, str) for item in data):
            urls = data  # List of URL strings
        elif isinstance(data, dict) and 'urls' in data:
            urls = data['urls']  # Dictionary with 'urls' key
        else:
            # Try to find URLs in any string values
            urls = []
            def extract_urls_from_dict(d):
                for k, v in d.items():
                    if isinstance(v, str) and 'familysearch.org/ark:' in v:
                        urls.append(v)
                    elif isinstance(v, dict):
                        extract_urls_from_dict(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                extract_urls_from_dict(item)
                            elif isinstance(item, str) and 'familysearch.org/ark:' in item:
                                urls.append(item)
            
            if isinstance(data, dict):
                extract_urls_from_dict(data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        extract_urls_from_dict(item)
    
    except json.JSONDecodeError:
        # If not valid JSON, try to extract URLs using regex
        st.write("Not valid JSON, extracting URLs using regex...")
        urls = re.findall(r'"?(https://[^"]*familysearch\.org/ark:/[^"^,^\s]+)"?', content)
    
    # Extract the specific ID portion from each URL
    ids = []
    id_status = st.empty()
    
    for url in urls:
        # Use regex to extract the ID portion (format: 3:1:3Q9M-CSKM-D3ZB-F)
        match = re.search(r'3:1:([^/]+)', url)
        if match:
            id_value = match.group(1)
            ids.append(id_value)
            id_status.write(f"Extracted ID: {id_value}")
    
    total_msg = f"Total extracted IDs: {len(ids)}"
    id_status.write(total_msg)
    st.success(total_msg)
    
    return ids

def create_id_position_mapping(ids):
    """Create a mapping between each ID and its position in the original list."""
    return {id_value: i+1 for i, id_value in enumerate(ids)}

def create_temp_directory():
    """Create a temporary directory for storing downloaded images."""
    temp_dir = tempfile.mkdtemp()
    return temp_dir

def get_output_directory(town_name, date_period, letter_code):
    """Create and return the path to the output directory based on metadata."""
    # Create temporary folder for storing files
    temp_dir = create_temp_directory() if 'temp_dir' not in st.session_state else st.session_state.temp_dir
    st.session_state.temp_dir = temp_dir
    
    # Create folder name using the naming convention
    folder_name = f"{town_name}_{date_period}_{letter_code}"
    output_dir = os.path.join(temp_dir, folder_name)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir

def download_image(
    image_id, 
    base_url, 
    output_dir, 
    index, 
    town_name, 
    date_period, 
    letter_code,
    cookie=None, 
    authorization=None,
    retry_count=3,
    retry_delay=5
):
    """Download a single image with retry capability."""
    # Create the URL for this specific image
    image_url = base_url.replace("{IDs}", image_id)
    
    # Set up headers with authentication
    headers = {}
    if cookie:
        headers['Cookie'] = cookie
    if authorization:
        headers['Authorization'] = authorization
    
    # Define file paths
    new_filename = f"{town_name}_{date_period}_{letter_code}_{index:03d}.jpg"
    new_path = os.path.join(output_dir, new_filename)
    
    # Try to download with retries
    for attempt in range(retry_count):
        try:
            # Download the image with authentication headers
            response = requests.get(image_url, headers=headers, stream=False)
            response.raise_for_status()
            
            # Convert to PIL Image for saving
            image = Image.open(io.BytesIO(response.content))
            
            # Save the image
            image.save(new_path)
            
            # Store image binary data in session state for direct download
            st.session_state.download_progress["image_data"][image_id] = {
                "filename": new_filename,
                "data": response.content  # Store binary image data
            }
            
            # Return success info and the path
            return True, new_path
        
        except Exception as e:
            # If this is not the last attempt, wait and retry
            if attempt < retry_count - 1:
                st.warning(f"Error downloading image {image_id} (attempt {attempt+1}/{retry_count}): {e}")
                st.info(f"Waiting {retry_delay} seconds before retry...")
                time.sleep(retry_delay)
            else:
                st.error(f"Error downloading image {image_id}: All {retry_count} attempts failed. Last error: {e}")
    
    # If we get here, all retry attempts failed
    return False, None

def save_download_progress():
    """Save current download progress to session state and file."""
    save_session_state()

def download_images():
    """Download images with Streamlit progress indicators."""
    # Get parameters from session state
    metadata = st.session_state.download_progress["metadata"]
    town_name = metadata["town_name"]
    date_period = metadata["date_period"]
    letter_code = metadata["letter_code"]
    ids = st.session_state.extracted_ids
    
    # Get previously completed ids
    completed_ids = [item[0] for item in st.session_state.download_progress["completed"]]
    failed_ids = st.session_state.download_progress["failed"]
    
    # ID position mapping
    id_position_map = st.session_state.download_progress["id_position_map"]
    
    # Filter out IDs that have already been downloaded
    pending_ids = [id for id in ids if id not in completed_ids]
    
    # Base URL and authorization from inputs
    base_url = 'https://sg30p0.familysearch.org/service/records/storage/deepzoomcloud/dz/v1/3:1:{IDs}/$dist'
    authorization = st.session_state.authorization
    cookie = None  # Optional
    
    # Get delay between downloads
    delay_between_downloads = st.session_state.delay_between_downloads
    
    # Get output directory based on metadata
    output_dir = get_output_directory(town_name, date_period, letter_code)
    
    if len(pending_ids) == 0:
        st.success("All images already downloaded!")
        return
    
    # Create progress bar
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Create columns for stats
    col1, col2, col3 = st.columns(3)
    success_count = col1.empty()
    failure_count = col2.empty()
    remaining_count = col3.empty()
    
    # Initialize counters
    successful = st.session_state.download_progress["completed"].copy()
    failed = failed_ids.copy()
    
    # Update initial counts
    success_count.metric("Successfully Downloaded", len(successful))
    failure_count.metric("Failed Downloads", len(failed))
    remaining_count.metric("Remaining", len(pending_ids))
    
    # Initialize pause state if not present
    if 'download_paused' not in st.session_state:
        st.session_state.download_paused = False
    
    # Add pause/resume button outside the download loop
    button_container = st.empty()
    
    # Check if already paused to determine initial button state
    if st.session_state.download_paused:
        if button_container.button("Resume Downloads", key="resume_button_main"):
            st.session_state.download_paused = False
            st.rerun()
    else:
        if button_container.button("Pause Downloads", key="pause_button_main"):
            st.session_state.download_paused = True
            save_download_progress()
            st.rerun()
    
    # Don't start the download loop if paused
    if st.session_state.download_paused:
        status_text.warning("Downloads paused. Click 'Resume Downloads' to continue.")
        return
    
    # Display individual download buttons section
    individual_downloads = st.expander("Individual Image Downloads")
    with individual_downloads:
        st.write("Download images individually:")
        # Create a container for individual download buttons
        download_buttons_container = st.empty()
    
    try:
        # Download loop with progress updates
        for i, image_id in enumerate(pending_ids):
            # Check if we should stop due to pause - before each download
            if st.session_state.download_paused:
                status_text.warning("Downloads paused. Click 'Resume Downloads' to continue.")
                save_download_progress()
                break
            
            # Update status
            progress_percent = int(100 * i / len(pending_ids))
            progress_bar.progress(progress_percent)
            status_text.write(f"Downloading image {i+1}/{len(pending_ids)}: ID {image_id}")
            
            # Get position from mapping
            position = id_position_map.get(image_id, i+1)
            
            # Download the image
            success, path = download_image(
                image_id, 
                base_url, 
                output_dir, 
                position,
                town_name, 
                date_period, 
                letter_code,
                cookie, 
                authorization
            )
            
            # Update tracking
            if success:
                successful.append((image_id, path))
                st.session_state.download_progress["completed"] = successful
                status_text.success(f"Successfully downloaded: {image_id}")
                
                # Update individual download buttons
                with individual_downloads:
                    # Create a download button for this image
                    filename = os.path.basename(path)
                    if image_id in st.session_state.download_progress["image_data"]:
                        img_data = st.session_state.download_progress["image_data"][image_id]["data"]
                        st.download_button(
                            label=f"Download {filename}",
                            data=img_data,
                            file_name=filename,
                            mime="image/jpeg",
                            key=f"dl_btn_{image_id}"
                        )
            else:
                failed.append(image_id)
                st.session_state.download_progress["failed"] = failed
                status_text.error(f"Failed to download: {image_id}")
            
            # Update metrics
            success_count.metric("Successfully Downloaded", len(successful))
            failure_count.metric("Failed Downloads", len(failed))
            remaining_count.metric("Remaining", len(pending_ids) - (i + 1))
            
            # Save progress
            save_download_progress()
            
            # Add delay between downloads
            if i < len(pending_ids) - 1:
                status_text.info(f"Waiting {delay_between_downloads} seconds before next download...")
                time.sleep(delay_between_downloads)
        
        # Complete the progress bar if all downloads were processed
        if not st.session_state.download_paused:
            progress_bar.progress(100)
            status_text.success("Download session complete!")
            st.session_state.download_paused = False
        
    except Exception as e:
        st.error(f"Download interrupted: {e}")
    finally:
        # Final summary
        st.write("---")
        st.write(f"Download summary: Successfully downloaded {len(successful)}/{len(ids)} images.")
        if failed:
            st.write(f"Failed to download {len(failed)} images.")
            st.write("Failed IDs:", failed[:5], "..." if len(failed) > 5 else "")

# Function to manually retry failed downloads
def retry_failed_downloads():
    """Move failed downloads back to the pending queue for another attempt."""
    failed_ids = st.session_state.download_progress["failed"]
    
    if not failed_ids:
        st.warning("There are no failed downloads to retry.")
        return
    
    # Show confirmation
    st.session_state.download_progress["failed"] = []
    st.success(f"Moved {len(failed_ids)} failed downloads back to the pending queue.")
    save_session_state()
    
    # Rerun the app to refresh the UI
    st.rerun()
            
def create_download_zip(include_completed_only=False):
    """Create a zip file of all downloaded images and return the binary data."""
    metadata = st.session_state.download_progress["metadata"]
    town_name = metadata["town_name"]
    date_period = metadata["date_period"]
    letter_code = metadata["letter_code"]
    
    # Create a temporary zip file in memory
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w') as zipf:
        # Use image data stored in session state
        if include_completed_only:
            for image_id, _ in st.session_state.download_progress["completed"]:
                if image_id in st.session_state.download_progress["image_data"]:
                    img_info = st.session_state.download_progress["image_data"][image_id]
                    filename = img_info["filename"]
                    data = img_info["data"]
                    
                    # Add file to zip
                    zipf.writestr(filename, data)
    
    # Reset buffer position
    zip_buffer.seek(0)
    
    # Return the zip file data
    return zip_buffer.getvalue()

def create_download_link():
    """Create a download link for all downloaded images."""
    metadata = st.session_state.download_progress["metadata"]
    town_name = metadata["town_name"]
    date_period = metadata["date_period"]
    letter_code = metadata["letter_code"]
    
    zip_filename = f"{town_name}_{date_period}_{letter_code}.zip"
    
    # Create a zip file of all downloaded images
    zip_data = create_download_zip(include_completed_only=True)
    
    # Create a download button
    if len(st.session_state.download_progress["completed"]) > 0:
        st.download_button(
            label="Download All Images as ZIP",
            data=zip_data,
            file_name=zip_filename,
            mime="application/zip"
        )

def create_individual_download_buttons():
    """Create individual download buttons for each downloaded image."""
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Download Individual Images")
        
        # Get completed downloads
        completed = st.session_state.download_progress["completed"]
        
        if not completed:
            st.write("No images downloaded yet.")
            return
        
        # Display download buttons for each image
        for image_id, path in completed:
            if image_id in st.session_state.download_progress["image_data"]:
                img_info = st.session_state.download_progress["image_data"][image_id]
                filename = img_info["filename"]
                data = img_info["data"]
                
                st.download_button(
                    label=f"Download {filename}",
                    data=data,
                    file_name=filename,
                    mime="image/jpeg",
                    key=f"btn_{image_id}"
                )

# Function to handle the extract button click
def handle_extract_ids_button():
    """Function to handle the extraction of IDs from input"""
    st.write("Extract IDs button clicked")
    
    content = None
    if st.session_state.uploaded_file is not None:
        content = st.session_state.uploaded_file.getvalue().decode("utf-8")
    elif st.session_state.url_text:
        content = st.session_state.url_text
    
    if content:
        st.session_state.extracted_ids = extract_ids_from_urls(content)
        
        # Create ID position mapping and store in session state
        id_position_map = create_id_position_mapping(st.session_state.extracted_ids)
        st.session_state.download_progress["id_position_map"] = id_position_map
        
        # Initialize image data storage if not exists
        if "image_data" not in st.session_state.download_progress:
            st.session_state.download_progress["image_data"] = {}
        
        # Create temp directory if not exists
        if "temp_dir" not in st.session_state:
            st.session_state.temp_dir = create_temp_directory()
        
        # Save session state
        save_session_state()
        
        # Move to next step
        st.session_state.current_step = 2
    else:
        st.error("Please upload a file or paste URLs to continue.")

# Main UI
def main():
    st.title("FamilySearch Image Downloader")
    
    # Initialize download_paused state if not exists
    if 'download_paused' not in st.session_state:
        st.session_state.download_paused = False
    
    # Check for existing session
    has_previous_session = (len(st.session_state.extracted_ids) > 0 and 
                          len(st.session_state.download_progress["completed"]) > 0)
    
    # Show resume option at the top if there's a previous session
    if has_previous_session and st.session_state.current_step == 1:
        st.info("Previous download session found.")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("Resume Previous Download"):
                # Move to the download step
                st.session_state.current_step = 3
                st.rerun()
                
        with col2:
            if st.button("Start New Download"):
                # Reset session state for a new download
                st.session_state.extracted_ids = []
                st.session_state.download_progress = {
                    "completed": [],
                    "failed": [],
                    "metadata": {
                        "town_name": "",
                        "date_period": "",
                        "letter_code": "",
                        "total_ids": 0
                    },
                    "id_position_map": {},
                    "image_data": {}
                }
                st.session_state.download_started = False
                st.session_state.download_paused = False
                st.session_state.current_step = 1
                
                # Clean up temp directory if it exists
                if "temp_dir" in st.session_state and os.path.exists(st.session_state.temp_dir):
                    try:
                        shutil.rmtree(st.session_state.temp_dir)
                    except:
                        pass
                st.session_state.temp_dir = create_temp_directory()
                
                save_session_state()
                st.rerun()
    
    # Debug info
    st.sidebar.write("Debug Info:")
    st.sidebar.write(f"Current Step: {st.session_state.current_step}")
    st.sidebar.write(f"Extracted IDs: {len(st.session_state.extracted_ids)}")
    st.sidebar.write(f"Completed Downloads: {len(st.session_state.download_progress['completed'])}")
    
    # Sidebar for configuration
    with st.sidebar:
        st.header("Configuration")
        
        # Authorization section
        st.subheader("Authorization")
        auth_input = st.text_input("Authorization Token (Bearer)", 
                             value=st.session_state.authorization, 
                             help="Format: Bearer p0-XXXX",
                             key="auth_input")
        if auth_input != st.session_state.authorization:
            st.session_state.authorization = auth_input
            save_session_state()
        
        # Download settings
        st.subheader("Download Settings")
        delay_input = st.number_input("Delay between downloads (seconds)", 
                             min_value=0.1, 
                             max_value=10.0, 
                             value=st.session_state.delay_between_downloads,
                             step=0.1,
                             key="delay_input")
        if delay_input != st.session_state.delay_between_downloads:
            st.session_state.delay_between_downloads = delay_input
            save_session_state()
    
    # Main content area - multi-step wizard
    if st.session_state.current_step == 1:
        # Step 1: Upload URLs and extract IDs
        st.header("Step 1: Upload FamilySearch URLs")
        
        st.write("Upload a file containing FamilySearch URLs or paste them directly below.")
        
        # File uploader
        st.session_state.uploaded_file = st.file_uploader("Upload a file with URLs", type=["txt", "json"], key="uploader")
        
        # Text area for direct input
        st.session_state.url_text = st.text_area("Or paste URLs here", height=200, key="url_text_input")
        
        # Process button - use a callback function to handle the click
        extract_button = st.button("Extract IDs", key="extract_button", on_click=handle_extract_ids_button)
        
    elif st.session_state.current_step == 2:
        # Step 2: Configure file naming
        st.header("Step 2: Configure File Naming")
        
        # Display number of IDs extracted
        st.write(f"Number of IDs extracted: {len(st.session_state.extracted_ids)}")
        
        # Naming parameters
        col1, col2, col3 = st.columns(3)
        
        with col1:
            town_name = st.text_input("Town Name", 
                                      value=st.session_state.download_progress["metadata"]["town_name"],
                                      key="town_name_input")
        
        with col2:
            date_period = st.text_input("Date Period", 
                                        value=st.session_state.download_progress["metadata"]["date_period"],
                                        key="date_period_input")
        
        with col3:
            letter_code = st.text_input("Letter Code", 
                                        value=st.session_state.download_progress["metadata"]["letter_code"],
                                        key="letter_code_input")
        
        # Validate and store parameters
        if st.button("Start Download", key="start_download_button"):
            if not all([town_name, date_period, letter_code]):
                st.error("All naming fields are required.")
            else:
                # Update metadata
                st.session_state.download_progress["metadata"] = {
                    "town_name": town_name,
                    "date_period": date_period,
                    "letter_code": letter_code,
                    "total_ids": len(st.session_state.extracted_ids)
                }
                
                # Save session state
                save_session_state()
                
                # Move to download step
                st.session_state.current_step = 3
                st.session_state.download_started = True
                st.rerun()
            
        # Back button
        if st.button("Back to Step 1", key="back_button"):
            st.session_state.current_step = 1
            save_session_state()
            st.rerun()
            
    elif st.session_state.current_step == 3:
        # Step 3: Download process
        st.header("Step 3: Download Images")
        
        # Display configuration summary
        metadata = st.session_state.download_progress["metadata"]
        st.write(f"Town: **{metadata['town_name']}** | Period: **{metadata['date_period']}** | Code: **{metadata['letter_code']}**")
        st.write(f"Total IDs to download: **{len(st.session_state.extracted_ids)}**")
        
        # Show progress summary
        completed_count = len(st.session_state.download_progress["completed"])
        total_count = len(st.session_state.extracted_ids)
        st.write(f"Download progress: **{completed_count}/{total_count}** images downloaded")
        
        # Handle download state
        if st.session_state.download_paused:
            # Show resume button when paused
            if st.button("Resume Downloads", key="resume_button"):
                st.session_state.download_paused = False
                st.rerun()
        elif not st.session_state.download_started:
            # Start download button if not started
            if st.button("Start Download", key="start_download_button_step3"):
                st.session_state.download_started = True
                save_session_state()
                st.rerun()
        else:
            # Display download section when running
            download_images()
        
        # Provide download link for completed downloads
        st.write("---")
        st.subheader("Download Options")

        # section for retry failed downloads
        if len(st.session_state.download_progress["failed"]) > 0:
            st.write("---")
            st.subheader("Failed Downloads")
            st.write(f"There are **{len(st.session_state.download_progress['failed'])}** failed downloads.")
            
            # Display the failed IDs
            with st.expander("View failed download IDs"):
                for i, failed_id in enumerate(st.session_state.download_progress["failed"]):
                    st.write(f"{i+1}. {failed_id}")
            
            # Add retry button
            if st.button("Retry Failed Downloads", key="retry_failed_btn"):
                retry_failed_downloads()
        
        # Show zip download option
        if completed_count > 0:
            st.write("Option 1: Download all images as a single ZIP file")
            create_download_link()
            
            # Show individual download options
            st.write("Option 2: Download individual images")
            # Create a container for individual download buttons in an expandable section
            with st.expander("Individual Image Downloads"):
                if len(st.session_state.download_progress["completed"]) > 0:
                    # Display buttons in a grid layout
                    num_cols = 3
                    cols = st.columns(num_cols)
                    
                    for i, (image_id, path) in enumerate(st.session_state.download_progress["completed"]):
                        if image_id in st.session_state.download_progress["image_data"]:
                            img_info = st.session_state.download_progress["image_data"][image_id]
                            filename = img_info["filename"]
                            data = img_info["data"]
                            
                            # Distribute buttons across columns
                            col_idx = i % num_cols
                            with cols[col_idx]:
                                st.download_button(
                                    label=f"Download {filename}",
                                    data=data,
                                    file_name=filename,
                                    mime="image/jpeg",
                                    key=f"img_btn_{image_id}"
                                )
        
        # Reset button
        st.write("---")
        if st.button("Start New Download", key="reset_button"):
            # Reset state for new download
            st.session_state.extracted_ids = []
            st.session_state.download_progress = {
                "completed": [],
                "failed": [],
                "metadata": {
                    "town_name": "",
                    "date_period": "",
                    "letter_code": "",
                    "total_ids": 0
                },
                "id_position_map": {},
                "image_data": {}
            }
            st.session_state.download_started = False
            st.session_state.download_paused = False
            st.session_state.current_step = 1
            
            # Clean up temp directory if it exists
            if "temp_dir" in st.session_state and os.path.exists(st.session_state.temp_dir):
                try:
                    shutil.rmtree(st.session_state.temp_dir)
                except:
                    pass
            st.session_state.temp_dir = create_temp_directory()
            
            save_session_state()
            st.rerun()

# Handle cleanup when the app is stopped
def on_exit():
    """Clean up temporary files when the app is stopped."""
    if "temp_dir" in st.session_state and os.path.exists(st.session_state.temp_dir):
        try:
            shutil.rmtree(st.session_state.temp_dir)
            cleanup_google_drive_state
        except:
            pass

# Register cleanup function
import atexit
atexit.register(on_exit)

if __name__ == "__main__":
    main()