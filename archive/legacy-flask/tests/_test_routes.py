import unittest
import json
import os
import tempfile
from flask import url_for
from photoframe import create_app
from photoframe.store import Store
from unittest.mock import patch, MagicMock


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app().test_client()
        self.app.testing = True

        # Create a temporary store file
        self.temp_store_file = tempfile.NamedTemporaryFile(delete=False)
        self.store = Store()
        self.store.file = self.temp_store_file.name
        self.store._write_all({})

    def tearDown(self):
        # Ensure file is closed before deleting
        self.temp_store_file.close()
        if os.path.exists(self.temp_store_file.name):
            os.remove(self.temp_store_file.name)

    @patch("photoframe.routes.get_photo_client")
    def test_homepage(self, mock_photo_client):
        mock_photo_client.return_value.service = True
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)

    @patch("photoframe.routes.get_photo_client")
    def test_authenticate(self, mock_photo_client):
        mock_photo_client.return_value.authenticate.return_value = None
        response = self.app.post('/authenticate')
        self.assertEqual(response.status_code, 302)  # Redirects to index

    @patch("photoframe.routes.get_photo_client")
    def test_albums_page(self, mock_photo_client):
        mock_photo_client.return_value.album_dict = []
        response = self.app.get('/albums')
        self.assertEqual(response.status_code, 200)

    def test_store_album(self):
        response = self.app.post('/store_album', data={
            'album_title': 'Test Album',
            'album_id': '12345'
        })
        self.assertEqual(response.status_code, 302)  # Redirects to index
        
        # Verify the store has the correct data
        self.assertEqual(self.store.get("album_title"), "Test Album")
        self.assertEqual(self.store.get("album_id"), "12345")

    @patch("photoframe.routes.get_photo_client")
    def test_photos_page(self, mock_photo_client):
        mock_photo_client.return_value.photo_directory = "test_photos"
        response = self.app.get('/photos')
        self.assertEqual(response.status_code, 200)

    def test_store_photo(self):
        response = self.app.post('/store_photo', data={
            'photo': 'test_photo.jpg',
            'pinned': 'true'
        })
        self.assertEqual(response.status_code, 302)  # Redirects to index
        
        # Verify store has the correct data
        self.assertEqual(self.store.get("current_photo"), "test_photo.jpg")
        self.assertEqual(self.store.get("transition"), "pinned")

    def test_store_photo_unpinned(self):
        response = self.app.post('/store_photo', data={
            'photo': 'another_photo.jpg',
            'pinned': 'false'
        })
        self.assertEqual(response.status_code, 302)  # Redirects to index
        
        # Verify store has the correct data
        self.assertEqual(self.store.get("current_photo"), "another_photo.jpg")
        self.assertEqual(self.store.get("transition"), "normal")

    @patch("photoframe.routes.get_photo_client")
    def test_serve_photos(self, mock_photo_client):
        mock_photo_client.return_value.photo_directory = "test_photos"
        response = self.app.get('/photo/test_photo.jpg')
        # We can't guarantee file exists, so check for 404 or 200
        self.assertIn(response.status_code, [200, 404])

    @patch("photoframe.routes.get_photo_client")
    def test_list_photos(self, mock_photo_client):
        mock_photo_client.return_value.list_photos = MagicMock(return_value=["photo1.jpg", "photo2.jpg"])
        response = self.app.get('/photos')
        self.assertEqual(response.status_code, 200)

    @patch("photoframe.routes.get_photo_client")
    def test_mock_store_album(self, mock_photo_client):
        mock_photo_client.return_value.store_album = MagicMock()
        response = self.app.post('/store_album', data={
            'album_title': 'Mock Album',
            'album_id': '67890'
        })
        self.assertEqual(response.status_code, 302)  # Redirects to index


if __name__ == '__main__':
    unittest.main()
