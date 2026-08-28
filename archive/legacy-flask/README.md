# Photoframe
[![Run Tests](https://github.com/mravalanche/photoframe/actions/workflows/test.yaml/badge.svg)](https://github.com/mravalanche/photoframe/actions/workflows/test.yaml)  

This library allows you to use a Pimoroni Inky Impression eInk display, as a digital photoframe.  
If I get it working correctly, we'll be able to interface with Google Photos, pull all the pictures out of an album, and play them in a random order.

## Hardware
Really simple hardware for this one:
- A [Pimoroni Inky Impression](https://shop.pimoroni.com/products/inky-impression-7-3) (7.3" for my deployment)
- A Raspberry Pi Zero 2 W (I'm using the one with the [pre-soldered header from PiHut](https://thepihut.com/products/raspberry-pi-zero-2?variant=43855634497731))
- A micro-usb cable and compatible power supply
- An 8" photoframe to put everything in

## Pre-Requisites

### Authenticate with Google Cloud

We need API access to Photos on Google Cloud.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and log in with your account
1. Make a new project:
    1. Click "Select a project" in the top left, and click "New project"
    1. Give it a name (something like "photoframe") and follow the prompts to continue
    1. Select the project, once created ("Select a project")
1. Enable the Google Photos API
    1. Select "Enabled APIs and service" on the left, or search
    1. Click "Enable APIs and Services" at the top of the page
    1. Search for `photoslibrary.googleapis.com` and select "Google Photos Library API"
    1. Click "Enable"
1. Create some credentials
    1. Under the same API menu on the left, click "Credentials" then "Create Credentials" selecting "OAuth client ID" from the dropdown
    1. If prompted, follow the instructions to create a consent screen
    1. Create an OAuth client ID, with application type of "Desktop app"
    1. Click on your client, and save the client ID and secret in your .env file. It should look like the .env file example below
1. Enable data access
    1. Go to the "Data access" tab on the left (under Google Auth Platform)
    1. Click "add or remove scopes"
    1. Search for `photoslibrary.readonly` in the filter, and tick the scope, clicking "Update" to finish
1. Go to "audience", and add your email as a "Test user"


```.env
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
ALBUM_ID=your-google-photos-album-id
```



## Setup

Todo.

### Clone this library

Wherever works for you (probably your home directory) clone this library:
```bash
git clone https://github.com/mravalanche/photoframe.git
```

### Set up your python virtual environment

The pimoroni libraries don't like be run from the system python, so instead should be run from a .venv, so lets set that up:

```bash
cd <directory for photoframe>
python -m venv .venv
```

## Todo

- [x] Add Google Photos Interface
- [ ] Try not to leak any creds
- [ ] Add Pimoroni Interface
- [ ] Write pre-requisite instructions
- [ ] Write setup instruction
- [ ] Do everything else in the universe
- [ ] Write tests
