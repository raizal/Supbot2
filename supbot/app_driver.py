"""
app_driver.py

provides an interface for the lower level appium calls to other systems of supbot
will be reworked to handle more exceptions
"""
import logging
import os
import re
import shlex
import subprocess
import threading
import traceback
from functools import wraps
from typing import Tuple, Optional, List

from appium.webdriver.extensions.keyboard import Keyboard
from appium.webdriver.webelement import WebElement
from appium.webdriver import Remote
import time
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver import ActionChains

from supbot import g, helper
# noinspection PyBroadException
from supbot.exceptions import DeviceNotFound

# noinspection PyBroadException
from supbot.helper import kill_thread


def check_delay(f):
    @wraps(f)
    def decorated_function(self, *args, **kwargs):
        self.driver.implicitly_wait(self.implicit_wait)
        result = f(self, *args, **kwargs)
        self.driver.implicitly_wait(1)
        return result

    return decorated_function


# noinspection PyBroadException
class AppDriver:
    """
    Abstracts appium calls
    """

    def __init__(self, driver: Remote, implicit_wait: int, appium_thread: Optional[threading.Thread], info: dict):
        self.driver = driver
        self.implicit_wait = implicit_wait
        self.appium_thread = appium_thread
        self.info = info

    @staticmethod
    def create() -> Optional['AppDriver']:
        """
        Initializes appium driver
        """
        # region input from kwargs
        # todo fix repetition of kwarg code
        run_server = not ("no_server" in g.kwargs and g.kwargs["no_server"])

        if "port" in g.kwargs and g.kwargs["port"] is not None:
            port = g.kwargs["port"]
        elif run_server:
            port = str(helper.get_free_tcp_port())
        else:
            port = "4723"

        g.logger.debug("Finding android device")

        if "device_name" in g.kwargs and g.kwargs["device_name"] is not None:
            device_name = g.kwargs["device_name"]
        elif "device" in g.kwargs and g.kwargs["device"] is not None:
            device_name = g.kwargs["device"]
        else:
            if 'ANDROID_HOME' not in os.environ.keys():
                g.logger.error("`ANDROID_HOME` environment variable not found "
                               "(default: %USERPROFILE%\AppData\Local\Android\Sdk)")
                raise
            adb_path = os.path.join(os.environ.get('ANDROID_HOME'), 'platform-tools', "adb")
            try:
                ada_output = subprocess.check_output([adb_path, "devices"]).decode('utf-8')
            except FileNotFoundError:
                g.logger.error("`ANDROID_HOME` environment variable not setup correctly "
                               "(default: %USERPROFILE%\AppData\Local\Android\Sdk)")
                raise
            search = re.search(r'^(.+)\tdevice', ada_output, flags=re.MULTILINE)
            if search is None:
                g.logger.error("No Android Device Found, Either specify using `device` "
                               "or make sure a device is available in adb")
                raise DeviceNotFound
            device_name = search.group(1)

        if "check_wait" in g.kwargs and g.kwargs["check_wait"] is not None:
            implicit_wait = g.kwargs["check_wait"]
        else:
            implicit_wait = 5

        # endregion

        appium_thread = None
        if run_server:
            def appium_logging():
                g.logger.debug("launching appium server on {}".format(port))
                try:
                    g.appium_process = subprocess.Popen(shlex.split(f"appium --bash-path /wd/hub --port {port}"),
                                                        stdout=subprocess.PIPE, shell=True)
                    appium_logs = logging.getLogger('appium')
                    while g.system.status > -1:
                        line = g.appium_process.stdout.readline().decode('utf-8')
                        appium_logs.debug(line)
                    g.appium_process.stdout.close()
                    g.appium_process.kill()
                    g.system.status = -2
                    g.logger.debug("appium server closed")
                except FileNotFoundError:
                    g.logger.error("Appium not installed, install node package manager, "
                                   "then use this command to install `npm install -g appium@1.15.1`")
                    raise

            appium_thread = threading.Thread(target=appium_logging)
            appium_thread.start()

        g.logger.debug("Connecting to appium with {}".format(device_name))
        desired_caps = {
            "platformName": "Android",
            "udid": device_name,
            "appPackage": "com.whatsapp",
            "appActivity": "com.whatsapp.HomeActivity",
            "noReset": True,
            "deviceName": "Android Emulator",
            "uiautomator2ServerInstallTimeout": 100000
        }
        try:
            driver = Remote('http://localhost:{}/wd/hub'.format(port), desired_caps)
        except WebDriverException as e:
            if "JAVA_HOME is not set currently" in e.msg:
                g.logger.error("`JAVA_HOME` environment variable not setup correctly "
                               "(default C:\PROGRA~1\Java\jdk1.8.0_181)")
            else:
                g.logger.error("Appium server could not be started because of unknown exception, please refer "
                               "'appium.log' file to troubleshoot. Alternatively you can run appium server as a "
                               "standalone and use `no_server` parameter. Refer Supbot2 docs for more info.")

            if not g.appium_process:
                g.appium_process.stdout.close()
                g.appium_process.kill()
            raise

        driver.implicitly_wait(1)
        g.logger.debug("driver created")
        return AppDriver(driver, implicit_wait, appium_thread, {"port": port, "device": device_name})

    def timeout_appium(self):
        if g.system.status != -2:
            time.sleep(5)
            if g.system.status != -2:
                kill_thread(self.appium_thread)
                g.logger.info("appium server killed")

    def destroy(self):
        """
        Quits appium drivers
        """
        self.driver.quit()

    def click_on_chat(self, chat_name: str, search:bool=False) -> bool:
        """
        Clicks on the chat list item in the app
        :param search: set true if on search screen
        :param chat_name: name of the contact
        """
        try:
            if not search:
                search = self.driver.find_elements_by_id("com.whatsapp:id/conversations_row_contact_name")
            else:
                search = self.driver.find_element_by_id("com.whatsapp:id/result_list").find_elements_by_id(
                    "com.whatsapp:id/conversations_row_contact_name")
            element = next(x for x in search if helper.contact_number_equal(x.text, chat_name))
            element.click()
            return True
        except Exception:
            return False

    def get_search_text(self):
        try:
            element = self.driver.find_element_by_id("com.whatsapp:id/search_input")
        except NoSuchElementException:
            try:
                # backwards compatibility
                g.logger.warning("Please update whatsapp version, older version might be a bit slow")
                element = self.driver.find_element_by_id("com.whatsapp:id/search_src_text")
            except NoSuchElementException:
                return None

        return element

    def type_in_search(self, chat_name: str) -> bool:
        element = self.get_search_text()

        if element is None:
            return False

        element.send_keys(chat_name)
        return True

    def click_search(self) -> bool:
        try:
            self.driver.find_element_by_id("com.whatsapp:id/menuitem_search").click()
            return True
        except Exception:
            return False

    def goto_home(self):
        self.driver.start_activity("com.whatsapp", "com.whatsapp.HomeActivity")

    def type_and_send(self, message: str, mentions: bool):
        """
        Entered text in chat, and presses the send button
        :param mentions:
        :param message:
        """
        try:
            element = self.driver.find_element_by_id('com.whatsapp:id/entry')

            if mentions:
                # break the message into parts depending on mentions
                parts = re.split(r"(^|\s)(@[A-Za-z0-9]+)", message)

                for p in parts:
                    # then try to press it after each mention string

                    actions = ActionChains(self.driver)
                    actions.send_keys_to_element(element, p)
                    actions.perform()

                    if re.match(r"(@[A-Za-z0-9]+)", p):
                        self.press_mention()
            else:
                element.send_keys(message)

            element = self.driver.find_element_by_id('com.whatsapp:id/send')
            element.click()

            return True
        except Exception:
            return False

    def click_on_last_chat_link(self):
        self.driver.find_elements_by_id("com.whatsapp:id/message_text").pop().click()
        return True

    def click_ok(self):
        try:
            ok = self.driver.find_element_by_id("android:id/button1")
            ok.click()
            return True
        except NoSuchElementException:
            return False

    def press_back(self):
        """
        presses the back button, then waits for animation/load to finish
        """
        time.sleep(0.5)
        self.driver.press_keycode(4)

    def press_chat_back(self):
        try:
            self.driver.find_element_by_id("com.whatsapp:id/back").click()
            return True
        except Exception:
            return False

    def press_search_back(self):
        try:
            self.driver.find_element_by_xpath("//android.widget.ImageView[@content-desc='Back'] or "
                                              "//android.widget.ImageButton[@content-des='Close']").click()
            return True
        except Exception:
            return False

    def press_mention(self):
        try:
            self.driver.find_element_by_id("com.whatsapp:id/mention_attach").click()
            return True
        except Exception:
            return False

    def get_new_chat(self) -> Optional[str]:
        """
        Checks for chat item with new message bubble,
        used by new_chat checker (checker system not made yet)
        :return: chat (contact_name) who sent a new chat
        """
        try:
            self.driver.implicitly_wait(1)
            element = self.driver.find_element_by_xpath('//android.widget.TextView[@resource-id='
                                                        '"com.whatsapp:id/conversations_row_message_count"]/../..'
                                                        '//android.widget.TextView[@resource-id="com.whatsapp:id'
                                                        '/conversations_row_contact_name"]')
            return element.text
        except NoSuchElementException:
            return None
        finally:
            self.driver.implicitly_wait(self.implicit_wait)

    def get_new_bubbles(self):
        return self.driver.find_elements_by_xpath('//android.widget.TextView[@resource-id='
                                                  '"com.whatsapp:id/unread_divider_tv"]/../..'
                                                  '//following-sibling::android.view.ViewGroup'
                                                  '//android.widget.LinearLayout[@resource-id='
                                                  '"com.whatsapp:id/main_layout"]')

    def get_new_messages(self) -> Optional[List[str]]:
        """
        checks for all the new messages
        :return: list of messages sent to the bot
        """
        try:
            new_bubbles = self.get_new_bubbles()

            messages = [Bubble(x).get_message() for x in new_bubbles]
            return messages
        except NoSuchElementException:
            return None

    def get_group_messages(self) -> Optional[List[Tuple[str, str]]]:
        try:
            new_bubbles = self.get_new_bubbles()
            messages = []
            for i, bubble in enumerate(new_bubbles):
                b = Bubble(bubble)
                messages.append((b.get_author(new_bubbles), b.get_message()))

            return messages
        except NoSuchElementException:
            return None

    def send_image(self, image_loc: str) -> bool:
        try:
            _, ext = os.path.splitext(image_loc)
            os.utime(image_loc, (time.time(), time.time()))
            self.driver.push_file(destination_path="/storage/emulated/0/Supbot/temp" + ext,
                                  source_path=image_loc)
            self.driver.find_element_by_id("com.whatsapp:id/input_attach_button").click()
            self.driver.find_element_by_id("com.whatsapp:id/pickfiletype_gallery").click()
            self.driver.find_element_by_xpath('//android.widget.TextView[@text="Supbot"]').click()
            self.driver.find_element_by_xpath('//android.widget.ImageView').click()
            self.driver.find_element_by_id("com.whatsapp:id/send").click()
            return True
        except Exception:
            return False

    def scroll_chat(self, reverse=False):
        try:
            elements = self.driver.find_elements_by_id("com.whatsapp:id/conversations_row_contact_name")
            if reverse:
                self.driver.scroll(elements[1], elements[-1], 3000)
            else:
                self.driver.scroll(elements[-1], elements[1], 3000)
            return True
        except Exception:
            return False

    # region checkers
    # todo make better architecture for check

    # todo divide this method into 2 decorators, 1 enables slow check (which u already did), other does the check
    #  logic in try except region helper
    def check(self, query, slow: bool = False, xpath: bool = False):
        """
        checks if an element is on screen
        :param xpath:
        :param query: id of the element
        :param slow: set to true if used for state.check methods
        :return: True if element exists
        """
        try:
            if slow:
                self.driver.implicitly_wait(self.implicit_wait)

            element = self.driver.find_element_by_id(query) if not xpath else self.driver.find_element_by_xpath(query)
            return element is not None
        except Exception:
            return False
        finally:
            if slow:
                self.driver.implicitly_wait(1)

    # endregion

    # region fast checker/ action checkers
    def check_scroll_end(self):
        return self.check("com.whatsapp:id/conversations_row_tip_tv")

    def check_scroll_top(self):
        """On top check is done by temp group pined on top"""
        try:
            search = self.driver.find_elements_by_id("com.whatsapp:id/conversations_row_contact_name")
            element = next(x for x in search if helper.contact_number_equal(x.text, "!temp"))
            return element is not None
        except Exception:
            return False

    def check_for_below_chat(self):
        return self.check("com.whatsapp:id/badge")

    def check_group(self) -> bool:
        return self.check("com.whatsapp:id/name_in_group_tv")

    # endregion

    # region slow checkers/ state checkers
    def check_fab(self):
        return self.check("com.whatsapp:id/fab", True)

    @check_delay
    def check_search_input(self):
        return self.get_search_text() is not None

    @check_delay
    def check_chat(self, chat_name):
        try:
            name = self.driver.find_element_by_id("com.whatsapp:id/conversation_contact_name").text
            return helper.contact_number_equal(name, chat_name)
        except Exception:
            return False
    # endregion

    # endregion


# noinspection PyBroadException
class Bubble:
    def __init__(self, web_element: WebElement):
        self.bubble = web_element

    def get_message(self) -> str:
        try:
            return self.bubble.find_element_by_id("com.whatsapp:id/message_text").text
        except Exception:
            return ""

    def _get_author_from_me(self) -> Optional[str]:
        try:
            return self.bubble.find_element_by_id("com.whatsapp:id/name_in_group_tv").text
        except Exception:
            return None

    def get_author(self, bubbles: List[WebElement]) -> Optional[str]:
        my_index = -1
        for bubble_i, bubble in enumerate(bubbles):
            if self.bubble == bubble:
                my_index = bubble_i

        checking = my_index
        while checking >= 0:
            author = Bubble(bubbles[checking])._get_author_from_me()
            if author is not None:
                return author
            checking -= 1

        return None
