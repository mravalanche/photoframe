import os
import pickle
import requests
import datetime
import threading
import json

from time import sleep
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from photoframe.store import store


GOOGLE_AUTH_CONFIG = {
    "scopes": ['https://www.googleapis.com/auth/photoslibrary.readonly'],
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": None,
}


class GooglePhotosAlbum:
    def __init__(self, album:dict):
        self.album_id:str = album.get("id", "<id_unknown>")
        self.title:str = album.get("title", "<title_unknown>")
        self.cover_photo_url:str = album.get("coverPhotoBaseUrl", "https://photos.google.com/favicon.ico")
        self.item_count:int = album.get("mediaItemsCount", 0)

    @property
    def thumbnail(self):
        return requests.get(f"{self.cover_photo_url}=w120-120")


class GooglePhotosClient:
    TOKEN_FILE = 'token.pickle'  # File to store authentication tokens

    def __init__(self, client_id, client_secret, app_route=None, local_directory="photos", album_id=None):
        self.client_id = client_id
        self.client_secret = client_secret
        
        # Photo Directory handling
        if app_route:
            self.photo_directory = os.path.join(app_route, local_directory)
        else:
            self.photo_directory = local_directory
        if not os.path.exists(self.photo_directory):
            os.makedirs(self.photo_directory, exist_ok=True)
        
        self.album_id = album_id
        self.service = None
        self.albums:list[GooglePhotosAlbum]|None = list()

        if store.get("authed"):
            self.authenticate()

        self._stop_watcher = False
        self._watcher_thread = threading.Thread(
            target=self._album_watcher,
            name="AlbumWatcher",
            daemon=True
        )
        self._watcher_thread.start()

    def _album_watcher(self):
        last_full_update = datetime.datetime(1,1,1)
        force_download = False
        check_interval = datetime.timedelta(minutes=30)

        while not self._stop_watcher:
            if self.album_id != store.get("album_id"):
                self.album_id = store.get("album_id")
            
            if self.album_id and (force_download or (datetime.datetime.now()-last_full_update > check_interval)):
                self.download_album_photos()
                last_full_update = datetime.datetime.now()
            
            sleep(5)

    def authenticate(self, force=False):
        creds = None

        # Load credentials from the pickle file if available and force is not set
        if os.path.exists(self.TOKEN_FILE) and not force:
            with open(self.TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)
        
        # Try to refresh the token if we can
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                force=True
        

        if not creds or not creds.valid or force:
            config = {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uris": GOOGLE_AUTH_CONFIG.get("redirect_uris"),
                    "auth_uri": GOOGLE_AUTH_CONFIG.get("auth_uri"),
                    "token_uri": GOOGLE_AUTH_CONFIG.get("token_uri"),
                }
            }
            print(f"{config = }")
            flow = Flow.from_client_config(
                config,
                scopes=GOOGLE_AUTH_CONFIG.get("scopes"),
                redirect_uri=GOOGLE_AUTH_CONFIG.get("redirect_uris")
            )

            auth_url, _ = flow.authorization_url(prompt="consent")

            return auth_url
        
        with open(self.TOKEN_FILE, 'wb+') as token:
            pickle.dump(creds, token)
        
        self.service = build('photoslibrary', 'v1', credentials=creds, static_discovery=False)
        store.set("authed", True)


    def complete_authentication(self, auth_response_url):
        oauth_data = store.get("oauth_data")
        if not oauth_data:
            return None
        
        config = {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uris": GOOGLE_AUTH_CONFIG.get("redirect_uris"),
                    "auth_uri": GOOGLE_AUTH_CONFIG.get("auth_uri"),
                    "token_uri": GOOGLE_AUTH_CONFIG.get("token_uri"),
                }
            }
        print(f"{config = }")
        flow = Flow.from_client_config(
            config,
            scopes=GOOGLE_AUTH_CONFIG.get("scopes"),
            redirect_uri=GOOGLE_AUTH_CONFIG.get("redirect_uris")
            )
        flow.fetch_token(authorization_response=auth_response_url)

        creds = flow.credentials
        with open(self.TOKEN_FILE, 'wb+') as token:
            pickle.dump(creds, token)
        
        store.set("authed", True)
        self.service = build('photoslibrary', 'v1', credentials=creds, static_discovery=False)
    
        
    def _get_albums(self):
        albums = self.service.albums().list(pageSize=50).execute().get('albums', [])
        self.albums = [GooglePhotosAlbum(x) for x in albums]
    
    @property
    def album_dict(self):
        if not self.albums:
            self._get_albums()
        albums = list()
        for album in self.albums:
            albums.append({
                "album_id": album.album_id,
                "title": album.title,
                "cover_photo_url": album.cover_photo_url,
                "item_count": album.item_count
            })
        return albums

    def list_albums(self):
        """
        List albums for given user
        """
        if not self.albums:
            self._get_albums()
            

        max_album_name_len = max([len(x.title) for x in self.albums])
        for album in self.albums:
            print(f"{album.title:<{max_album_name_len}} ({album.item_count:>4}): {album.album_id}")
    

    def get_album_photos(self):
        """
        Fetch all media items in the specified album.
        Returns a list of media items (dicts).
        """
        if not self.service:
            raise RuntimeError("Google Photos service is not authenticated.")

        media_items = []
        next_page_token = None

        try:
            while True:
                response = self.service.mediaItems().search(
                    body={
                        'albumId': self.album_id,
                        'pageSize': 100,
                        'pageToken': next_page_token
                    }
                ).execute()

                media_items.extend(response.get('mediaItems', []))
                next_page_token = response.get('nextPageToken')

                if not next_page_token:
                    break

        except HttpError as e:
            raise RuntimeError(f"Failed to fetch album photos: {e}")

        return media_items

    def download_album_photos(self):
        """
        Download all photos from the album to the local directory.
        Updates existing files, removes files no longer in the album, and handles errors.
        """
        
        if not os.path.exists(self.photo_directory):
            os.makedirs(self.photo_directory)

        try:
            # Get the list of photos in the album
            album_photos = self.get_album_photos()

            # Get the filenames of the photos currently in the local directory
            local_files = set(os.listdir(self.photo_directory))
            remote_files = set()

            for photo in album_photos:
                filename = photo.get('filename')
                base_url = photo.get('baseUrl')

                if not filename or not base_url:
                    continue

                remote_files.add(filename)
                local_path = os.path.join(self.photo_directory, filename)

                # Download or update the file if it doesn't exist locally
                if filename not in local_files or not os.path.exists(local_path):
                    response = requests.get(f"{base_url}=d")  # Add '=d' for download
                    if response.status_code == 200:
                        with open(local_path, 'wb') as file:
                            file.write(response.content)
                            print(f"Downloaded: {filename}")

                    else:
                        print(f"Failed to download: {filename}")

            # Remove files that are no longer in the remote album
            files_to_remove = local_files - remote_files
            for file_to_remove in files_to_remove:
                os.remove(os.path.join(self.photo_directory, file_to_remove))
                print(f"Removed: {file_to_remove}")

        except RuntimeError as e:
            print(f"Error: {e}. No files will be deleted.")
        except Exception as e:
            print(f"Unexpected error: {e}. No files will be deleted.")
        
        store.set("downloaded", True)
    
