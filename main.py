import riot_api,utils
import asyncio 
import json
import base64
import pypresence
import time
import threading
import pystray
from pystray import Icon as icon, Menu as menu, MenuItem as item
from PIL import Image, ImageDraw
import os
import subprocess
import psutil
import ctypes
import sys
import webserver
import oauth
import client_api
import match_session
from dotenv import load_dotenv
from psutil import AccessDenied
import nest_asyncio


nest_asyncio.apply()
load_dotenv()


global systray
systray = None
window_shown = False
client_id = str(os.environ.get('CLIENT_ID'))
client = None
last_presence = {}
session = None
last_state = None
loop = None
launch_timeout = 120


#weird workaround for getting image w/ relative path to work with pyinstaller
def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'): 
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# ----------------------------------------------------------------------------------------------
# console/taskbar control stuff!
# thanks for some of this pete (github/restrafes) :)
kernel32 = ctypes.WinDLL('kernel32')
user32 = ctypes.WinDLL('user32')
hWnd = kernel32.GetConsoleWindow()

# prevent interaction of the console window which pauses execution
kernel32 = ctypes.windll.kernel32
kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), 128)

# console visibility toggle functionality
def tray_window_toggle(icon, item):
    try:
        global window_shown
        window_shown = not item.checked
        if window_shown:
            user32.ShowWindow(hWnd, 1)
        else:
            user32.ShowWindow(hWnd, 0)
    except:
        pass

print("initializing systray object")
def run_systray():
    global systray, window_shown

    systray_image = Image.open(resource_path("favicon.ico"))
    systray_menu = menu(
        item('show debug', tray_window_toggle, checked=lambda item: window_shown),
        item('quit', close_program),
    )
    systray = pystray.Icon("valorant-rpc", systray_image, "valorant-rpc", systray_menu)
    systray.run()
print("systray ready!")

def close_program():
    global systray,client
    user32.ShowWindow(hWnd, 1)
    client.close()
    systray.stop()
    sys.exit()
#end sys tray stuff
# ----------------------------------------------------------------------------------------------



def update_rpc(data):

    global session

    #party state
    

 
    if not data["isIdle"]:
        #menu
        if data["sessionLoopState"] == "MENUS" and data["partyState"] != "CUSTOM_GAME_SETUP":
            client.set_activity(
                state=data["party_state"],
                details=("In Queue" if data["partyState"] == "MATCHMAKING" else "Lobby") + (f" - {data['queue_id']}" if data["queue_id"] else ""),
                start=data["time"] if not data["time"] == False else None,
                large_image=("game_icon_white" if data["partyState"] == "MATCHMAKING" else "game_icon"),
                large_text="VALORANT",
                small_image="crown_icon" if utils.validate_party_size(data) else None,
                small_text="Party Leader" if utils.validate_party_size(data) else None,
                party_id=data["partyId"],
                party_size=data["party_size"],
                join=data["join_state"]
            )

        #custom setup
        elif data["sessionLoopState"] == "MENUS" and data["partyState"] == "CUSTOM_GAME_SETUP":
            game_map = utils.maps[data["matchMap"].split("/")[-1]]
            client.set_activity(
                state=data["party_state"],
                details="Lobby" + (f" - {data['queue_id']}" if data['queue_id'] else ""),
                start=data["time"] if not data["time"] == False else None,
                large_image=f"splash_{game_map.lower()}_square",
                large_text=game_map,
                small_image="crown_icon" if utils.validate_party_size(data) else None,
                small_text="Party Leader" if utils.validate_party_size(data) else None,
                party_id=data["partyId"],
                party_size=data['party_size'],
                join=data['join_state']
            )

        elif data["sessionLoopState"] == "PREGAME":
            if last_state != "PREGAME":
                # new game session, create match object
                if session is None: 
                    session = match_session.Session(client)
                    session.init_pregame(data)
                    print('new sesh')


    elif data["isIdle"]:
        client.set_activity(
            state="Away",
            details="Lobby" + (f" - {data['queue_id']}" if data["queue_id"] else ""),
            large_image="game_icon",
            large_text="VALORANT",
            small_image="away_icon",
        )


def join_listener(data):
    config = utils.get_config()
    username = config['riot-account']['username']
    password = config['riot-account']['password']
    uuid,headers = client_api.get_auth(username,password)
    party_id = data['secret'].split('/')[1]
    print(party_id)
    client_api.post_glz(f'/parties/v1/players/{uuid}/joinparty/{party_id}',headers)
    #somehow this works!


def listen(lockfile):
    global last_presence,client,session
    while True:
        if not utils.is_process_running():
            print("valorant closed, exiting")
            close_program()

        #event listeners
        client.register_event('ACTIVITY_JOIN',join_listener)

        if session is None:
            #in the menus, waiting for match
            presence = riot_api.get_presence(lockfile)
            if presence == last_presence:
                last_presence = presence
                continue
            update_rpc(presence)
            last_presence = presence
            last_state = presence['sessionLoopState']
            time.sleep(1)

        elif session is not None:
            # match started, now use session object for updating presence
            # while in pregame update less often because less is changing and rate limits
            presence = riot_api.get_presence(lockfile)
            session.mainloop(presence)
            time.sleep(3)
        '''
        except Exception as e:
            print(e)
            if not utils.is_process_running():
                print("valorant closed, exiting")
                close_program()
                '''



# ----------------------------------------------------------------------------------------------
# startup
def main(loop):
    global client
    # setup client
    client = pypresence.Client(client_id,loop=loop) 
    webserver.run()
    client.start()
    oauth.authorize(client)
    
    launch_timer = 0

    #check if val is open
    if not utils.is_process_running():
        print("valorant not opened, attempting to run...")
        subprocess.Popen([utils.get_rcs_path(), "--launch-product=valorant", "--launch-patchline=live"])
        while not utils.is_process_running():
            print("waiting for valorant...")
            launch_timer += 1
            if launch_timer >= launch_timeout:
                close_program()
            time.sleep(1)

    #game launching, set loading presence
    client.set_activity(
        state="Loading",
        large_image="game_icon",
        large_text="valorant-rpc by @cm_an#2434"
    )

    #check for lockfile
    launch_timer = 0
    lockfile = riot_api.get_lockfile()
    if lockfile is None:
        while lockfile is None:
            print("waiting for lockfile...")
            lockfile = riot_api.get_lockfile()
            launch_timer += 1
            if launch_timer >= launch_timeout:
                close_program()
            time.sleep(1)
    print("lockfile loaded! hiding window...")
    #time.sleep(3)
    systray_thread = threading.Thread(target=run_systray)
    systray_thread.start()
    user32.ShowWindow(hWnd, 0)

    #check for presence
    launch_timer = 0
    presence = riot_api.get_presence(lockfile)
    if presence is None:
        while presence is None:
            print("waiting for presence...")
            presence = riot_api.get_presence(lockfile)
            launch_timer += 1
            if launch_timer >= launch_timeout:
                print("presence took too long, terminating program!")
                close_program()
            time.sleep(1)
    update_rpc(presence)
    print(f"LOCKFILE: {lockfile}")

    #start the loop
    listen(lockfile)

if __name__=="__main__":   
    loop = asyncio.get_event_loop()
    main(loop)
# ----------------------------------------------------------------------------------------------