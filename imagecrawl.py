import streamlit as st
import requests
from bs4 import BeautifulSoup
import os
from urllib.parse import urljoin, urlparse
import logging
import shutil
from typing import List, Optional
import pandas as pd
from datetime import datetime
import time
import base64

class ImageScraper:
    def __init__(self, base_url: str, download_path: str = "downloaded_images"):
        self.base_url = base_url
        self.download_path = download_path
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Create download directory if it doesn't exist
        os.makedirs(download_path, exist_ok=True)

    def get_page_content(self) -> Optional[BeautifulSoup]:
        try:
            response = self.session.get(self.base_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'lxml')
        except requests.exceptions.RequestException as e:
            st.error(f"Failed to fetch page: {str(e)}")
            return None

    def extract_image_urls(self, soup: BeautifulSoup) -> List[str]:
        image_urls = []
        if soup is None:
            return image_urls

        for img in soup.find_all('img'):
            src = img.get('src')
            if src:
                absolute_url = urljoin(self.base_url, src)
                image_urls.append(absolute_url)
        
        return image_urls

    def download_image(self, image_url: str, progress_bar) -> dict:
        try:
            filename = os.path.basename(urlparse(image_url).path)
            if not filename:
                filename = f"image_{hash(image_url)}.jpg"

            filepath = os.path.join(self.download_path, filename)
            
            response = self.session.get(image_url, headers=self.headers, stream=True, timeout=10)
            response.raise_for_status()

            with open(filepath, 'wb') as f:
                response.raw.decode_content = True
                shutil.copyfileobj(response.raw, f)

            file_size = os.path.getsize(filepath)
            progress_bar.progress(1.0)
            
            return {
                'filename': filename,
                'url': image_url,
                'size': f"{file_size / 1024:.1f} KB",
                'status': 'Success',
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

        except Exception as e:
            return {
                'filename': filename if 'filename' in locals() else 'Unknown',
                'url': image_url,
                'size': '0 KB',
                'status': f'Failed: {str(e)}',
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

def create_download_zip(directory):
    """Create a download link for the zip file containing all images"""
    import zipfile
    import io
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                zip_file.write(file_path, os.path.basename(file_path))
    
    zip_buffer.seek(0)
    return zip_buffer

def main():
    st.set_page_config(page_title="Web Image Scraper", layout="wide")
    
    st.title("🖼️ Web Image Scraper")
    st.markdown("""
    This app allows you to scrape and download images from any website. Simply enter the URL below and click 'Start Scraping'.
    """)

    # Input section
    with st.form("scraper_form"):
        url = st.text_input("Enter Website URL", placeholder="https://example.com")
        download_path = st.text_input("Download Directory", value="downloaded_images")
        submitted = st.form_submit_button("Start Scraping")

    if submitted and url:
        # Initialize scraper
        scraper = ImageScraper(url, download_path)
        
        # Create progress containers
        status_container = st.empty()
        progress_container = st.empty()
        results_container = st.empty()
        
        # Get page content
        status_container.info("Fetching webpage...")
        soup = scraper.get_page_content()
        
        if soup:
            # Extract image URLs
            image_urls = scraper.extract_image_urls(soup)
            status_container.info(f"Found {len(image_urls)} images")
            
            if image_urls:
                # Create a results dataframe
                results_df = pd.DataFrame(columns=['filename', 'url', 'size', 'status', 'timestamp'])
                
                # Download images with progress tracking
                for i, image_url in enumerate(image_urls, 1):
                    # Create progress bar for current image
                    progress_text = f"Downloading image {i}/{len(image_urls)}"
                    progress_bar = progress_container.progress(0.0)
                    status_container.info(progress_text)
                    
                    # Download image and get result
                    result = scraper.download_image(image_url, progress_bar)
                    results_df.loc[len(results_df)] = result
                    
                    # Update results display
                    results_container.dataframe(
                        results_df,
                        use_container_width=True,
                        hide_index=True
                    )
                    
                    time.sleep(0.1)  # Prevent too rapid requests
                
                # Final status update
                status_container.success("Scraping completed!")
                progress_container.empty()
                
                # Create download button for zip file
                zip_buffer = create_download_zip(download_path)
                zip_filename = "scraped_images.zip"
                
                st.download_button(
                    label="Download All Images",
                    data=zip_buffer,
                    file_name=zip_filename,
                    mime="application/zip",
                    key='download_button'
                )
                
                # Display statistics
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total Images Found", len(image_urls))
                with col2:
                    successful_downloads = len(results_df[results_df['status'] == 'Success'])
                    st.metric("Successfully Downloaded", successful_downloads)
                with col3:
                    failed_downloads = len(results_df[results_df['status'] != 'Success'])
                    st.metric("Failed Downloads", failed_downloads)
                
            else:
                st.warning("No images found on the specified webpage.")
        else:
            st.error("Failed to fetch webpage. Please check the URL and try again.")

if __name__ == "__main__":
    main()