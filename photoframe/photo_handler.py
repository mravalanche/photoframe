import os
import datetime
import time
from random import shuffle
from photoframe.store import store
from photoframe.common import log


def list_photos(photo_client):
    photo_dir = photo_client.photo_directory
    return [f for f in os.listdir(photo_dir) if os.path.isfile(os.path.join(photo_dir, f))]


def get_next_photo():
    pass


def photo_updater(photo_client):

    def _photo_list_gen(photo_list):
        while True:
            yield from photo_list

    # Wait to make sure we have photo list
    while not store.get("downloaded"):
        time.sleep(5)
    
    current_photo = store.get("current_photo")
    current_transition = store.get("transition")
    _photo = _photo_list_gen(list_photos(photo_client))
    while True:
        required_photo = store.get("current_photo")
        transition = store.get("transition")
        now = datetime.datetime.now()
        
        # If the current photo isn't what it's supposed to be, update it
        if current_photo != required_photo:
            log.debug(f"Required photo mismatch. Changing {current_photo} -> {required_photo}")
            current_photo = update_photo(required_photo)
            store.set("next_update", now+datetime.timedelta(seconds=store.get("speed", 1800)))
        
        # If the photo is supposed to be pinned, we don't need to do anything else
        if transition == "pinned":
            time.sleep(5)
            continue
        
        # If we've changed transition types, handle that by getting a new photo list generator
        if transition != current_transition:
            current_transition = transition
            log.debug(f"Updaeting transition: {current_transition} -> {transition}")
            
            # Shuffle photos if we're in random, otherwise just a list
            if transition == "normal":
                _photo = _photo_list_gen(list_photos(photo_client))
            elif transition == "random":
                _photo = _photo_list_gen(shuffle(list_photos(photo_client)))
            
            # Loop through the generator so we're at the current photo.
            # Do this a max of 1000 times, just to prevent an infinite loop if something goes wrong
            i = 0
            while _photo != current_photo:
                next(_photo)
                i += 1
                if i >= 1000: break

        # Get next photo if we're due an update, and update the next_update time
        if now >= store.get("next_update"):
            current_photo = update_photo(next(_photo))
            log.debug(f"Update time hit. Changing photo: {required_photo} -> {current_photo}")
            store.set("next_update", now+datetime.timedelta(seconds=store.get("speed", 1800)))
        time.sleep(5)


def update_photo(new_photo):
    store.set("current_photo", new_photo)
    return new_photo
