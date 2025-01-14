import datetime
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import sys
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from logging.handlers import RotatingFileHandler
from time import time
from typing import Optional, List

import pytz
import requests
from bs4 import BeautifulSoup
from flask import send_file, make_response

import utils.constants as constants
from utils.config import config


def get_logger(path, level=logging.ERROR, init=False):
    """
    get the logger
    """
    if not os.path.exists(constants.output_path):
        os.makedirs(constants.output_path)
    if init and os.path.exists(path):
        os.remove(path)
    handler = RotatingFileHandler(path, encoding="utf-8")
    logger = logging.getLogger(path)
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def format_interval(t):
    """
    Formats a number of seconds as a clock time, [H:]MM:SS

    Parameters
    ----------
    t  : int or float
        Number of seconds.
    Returns
    -------
    out  : str
        [H:]MM:SS
    """
    mins, s = divmod(int(t), 60)
    h, m = divmod(mins, 60)
    if h:
        return "{0:d}:{1:02d}:{2:02d}".format(h, m, s)
    else:
        return "{0:02d}:{1:02d}".format(m, s)


def get_pbar_remaining(n=0, total=0, start_time=None):
    """
    Get the remaining time of the progress bar
    """
    try:
        elapsed = time() - start_time
        completed_tasks = n
        if completed_tasks > 0:
            avg_time_per_task = elapsed / completed_tasks
            remaining_tasks = total - completed_tasks
            remaining_time = format_interval(avg_time_per_task * remaining_tasks)
        else:
            remaining_time = "未知"
        return remaining_time
    except Exception as e:
        print(f"Error: {e}")


def update_file(final_file, old_file, copy=False):
    """
    Update the file
    """
    old_file_path = resource_path(old_file, persistent=True)
    final_file_path = resource_path(final_file, persistent=True)
    if os.path.exists(old_file_path):
        if copy:
            shutil.copyfile(old_file_path, final_file_path)
        else:
            os.replace(old_file_path, final_file_path)


def filter_by_date(data):
    """
    Filter by date and limit
    """
    default_recent_days = 30
    use_recent_days = config.recent_days
    if not isinstance(use_recent_days, int) or use_recent_days <= 0:
        use_recent_days = default_recent_days
    start_date = datetime.datetime.now() - datetime.timedelta(days=use_recent_days)
    recent_data = []
    unrecent_data = []
    for (url, date, resolution, origin), response_time in data:
        item = ((url, date, resolution, origin), response_time)
        if date:
            date = datetime.datetime.strptime(date, "%m-%d-%Y")
            if date >= start_date:
                recent_data.append(item)
            else:
                unrecent_data.append(item)
        else:
            unrecent_data.append(item)
    recent_data_len = len(recent_data)
    if recent_data_len == 0:
        recent_data = unrecent_data
    elif recent_data_len < config.urls_limit:
        recent_data.extend(unrecent_data[: config.urls_limit - len(recent_data)])
    return recent_data


def get_soup(source):
    """
    Get soup from source
    """
    source = re.sub(
        r"<!--.*?-->",
        "",
        source,
        flags=re.DOTALL,
    )
    soup = BeautifulSoup(source, "html.parser")
    return soup


def get_resolution_value(resolution_str):
    """
    Get resolution value from string
    """
    try:
        if resolution_str:
            pattern = r"(\d+)[xX*](\d+)"
            match = re.search(pattern, resolution_str)
            if match:
                width, height = map(int, match.groups())
                return width * height
    except:
        pass
    return 0


def get_total_urls(info_list, ipv_type_prefer, origin_type_prefer):
    """
    Get the total urls from info list
    """
    origin_prefer_bool = bool(origin_type_prefer)
    if not origin_prefer_bool:
        origin_type_prefer = ["all"]
    categorized_urls = {
        origin: {"ipv4": [], "ipv6": []} for origin in origin_type_prefer
    }
    total_urls = []
    for url, _, resolution, origin in info_list:
        if not origin:
            continue

        if origin == "whitelist":
            w_url, _, w_info = url.partition("$")
            w_info_value = w_info.partition("!")[2] or "白名单"
            total_urls.append(add_url_info(w_url, w_info_value))
            continue

        if origin == "subscribe" and "/rtp/" in url:
            origin = "multicast"

        if origin_prefer_bool and (origin not in origin_type_prefer):
            continue

        pure_url, _, info = url.partition("$")
        if not info:
            origin_name = constants.origin_map[origin]
            if origin_name:
                url = add_url_info(pure_url, origin_name)

        url_is_ipv6 = is_ipv6(url)
        if url_is_ipv6:
            url = add_url_info(url, "IPv6")

        if resolution:
            url = add_url_info(url, resolution)

        if not origin_prefer_bool:
            origin = "all"

        if url_is_ipv6:
            categorized_urls[origin]["ipv6"].append(url)
        else:
            categorized_urls[origin]["ipv4"].append(url)

    ipv_num = {
        "ipv4": 0,
        "ipv6": 0,
    }
    urls_limit = config.urls_limit
    for origin in origin_type_prefer:
        if len(total_urls) >= urls_limit:
            break
        for ipv_type in ipv_type_prefer:
            if len(total_urls) >= urls_limit:
                break
            if ipv_num[ipv_type] < config.ipv_limit[ipv_type]:
                urls = categorized_urls[origin][ipv_type]
                if not urls:
                    break
                limit = min(
                    max(config.source_limits.get(origin, urls_limit) - ipv_num[ipv_type], 0),
                    max(config.ipv_limit[ipv_type] - ipv_num[ipv_type], 0),
                )
                limit_urls = urls[:limit]
                total_urls.extend(limit_urls)
                ipv_num[ipv_type] += len(limit_urls)
            else:
                continue

    if config.open_supply:
        ipv_type_total = list(dict.fromkeys(ipv_type_prefer + ["ipv4", "ipv6"]))
        if len(total_urls) < urls_limit:
            for origin in origin_type_prefer:
                if len(total_urls) >= urls_limit:
                    break
                for ipv_type in ipv_type_total:
                    if len(total_urls) >= urls_limit:
                        break
                    extra_urls = categorized_urls[origin][ipv_type][: config.source_limits.get(origin, urls_limit)]
                    total_urls.extend(extra_urls)
                    total_urls = list(dict.fromkeys(total_urls))[:urls_limit]

    total_urls = list(dict.fromkeys(total_urls))[:urls_limit]

    if not config.open_url_info:
        return [url.partition("$")[0] for url in total_urls]
    else:
        return total_urls


def get_total_urls_from_sorted_data(data):
    """
    Get the total urls with filter by date and depulicate from sorted data
    """
    total_urls = []
    if len(data) > config.urls_limit:
        total_urls = [url for (url, _, _, _), _ in filter_by_date(data)]
    else:
        total_urls = [url for (url, _, _, _), _ in data]
    return list(dict.fromkeys(total_urls))[: config.urls_limit]


def is_ipv6(url):
    """
    Check if the url is ipv6
    """
    try:
        host = urllib.parse.urlparse(url).hostname
        ipaddress.IPv6Address(host)
        return True
    except ValueError:
        return False


def check_ipv6_support():
    """
    Check if the system network supports ipv6
    """
    url = "https://ipv6.tokyo.test-ipv6.com/ip/?callback=?&testdomain=test-ipv6.com&testname=test_aaaa"
    try:
        print("Checking if your network supports IPv6...")
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            print("Your network supports IPv6")
            return True
    except Exception:
        pass
    print("Your network does not support IPv6, don't worry, these results will be saved")
    return False


def check_url_ipv_type(url):
    """
    Check if the url is compatible with the ipv type in the config
    """
    ipv6 = is_ipv6(url)
    ipv_type = config.ipv_type
    return (
            (ipv_type == "ipv4" and not ipv6)
            or (ipv_type == "ipv6" and ipv6)
            or ipv_type == "全部"
            or ipv_type == "all"
    )


def check_url_by_keywords(url, keywords=None):
    """
    Check by URL keywords
    """
    if not keywords:
        return True
    else:
        return any(keyword in url for keyword in keywords)


def merge_objects(*objects):
    """
    Merge objects
    """

    def merge_dicts(dict1, dict2):
        for key, value in dict2.items():
            if key in dict1:
                if isinstance(dict1[key], dict) and isinstance(value, dict):
                    merge_dicts(dict1[key], value)
                elif isinstance(dict1[key], set):
                    dict1[key].update(value)
                elif isinstance(dict1[key], list):
                    if value:
                        dict1[key].extend(x for x in value if x not in dict1[key])
                elif value:
                    dict1[key] = {dict1[key], value}
            else:
                dict1[key] = value

    merged_dict = {}
    for obj in objects:
        if not isinstance(obj, dict):
            raise TypeError("All input objects must be dictionaries")
        merge_dicts(merged_dict, obj)

    return merged_dict


def get_ip_address():
    """
    Get the IP address
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ip = "127.0.0.1"
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except:
        ip = "127.0.0.1"
    finally:
        s.close()
        return f"http://{ip}:{config.app_port}"


def convert_to_m3u(first_channel_name=None):
    """
    Convert result txt to m3u format
    """
    user_final_file = resource_path(config.final_file)
    if os.path.exists(user_final_file):
        with open(user_final_file, "r", encoding="utf-8") as file:
            m3u_output = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/fanmingming/live/main/e.xml"\n'
            current_group = None
            for line in file:
                trimmed_line = line.strip()
                if trimmed_line != "":
                    if "#genre#" in trimmed_line:
                        current_group = trimmed_line.replace(",#genre#", "").strip()
                    else:
                        try:
                            original_channel_name, _, channel_link = map(
                                str.strip, trimmed_line.partition(",")
                            )
                        except:
                            continue
                        processed_channel_name = re.sub(
                            r"(CCTV|CETV)-(\d+)(\+.*)?",
                            lambda m: f"{m.group(1)}{m.group(2)}"
                                      + ("+" if m.group(3) else ""),
                            first_channel_name if current_group == "🕘️更新时间" else original_channel_name,
                        )
                        m3u_output += f'#EXTINF:-1 tvg-name="{processed_channel_name}" tvg-logo="https://raw.githubusercontent.com/fanmingming/live/main/tv/{processed_channel_name}.png"'
                        if current_group:
                            m3u_output += f' group-title="{current_group}"'
                        m3u_output += f",{original_channel_name}\n{channel_link}\n"
            m3u_file_path = os.path.splitext(user_final_file)[0] + ".m3u"
            with open(m3u_file_path, "w", encoding="utf-8") as m3u_file:
                m3u_file.write(m3u_output)
            print(f"✅ M3U result file generated at: {m3u_file_path}")


def convert_to_jellyfin_m3u(first_channel_name=None):
    """
    Convert result txt to m3u format
    只保留一条channel数据，并且去除时间更新的字段
    """
    user_final_file = resource_path(config.final_file)
    if os.path.exists(user_final_file):
        with open(user_final_file, "r", encoding="utf-8") as file:
            m3u_output = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/fanmingming/live/main/e.xml"\n'
            current_group = None
            exist_channel_name_list = []
            for line in file:
                trimmed_line = line.strip()
                if trimmed_line != "":
                    if "#genre#" in trimmed_line:
                        current_group = trimmed_line.replace(",#genre#", "").strip()
                        # 去除更新时间
                        if current_group.find("更新时间") > -1:
                            current_group = None
                            continue
                    else:
                        if current_group is None:
                            continue
                        try:
                            original_channel_name, _, channel_link = map(
                                str.strip, trimmed_line.partition(",")
                            )
                        except:
                            continue
                        processed_channel_name = re.sub(
                            r"(CCTV|CETV)-(\d+)(\+.*)?",
                            lambda m: f"{m.group(1)}{m.group(2)}"
                                      + ("+" if m.group(3) else ""),
                            first_channel_name if current_group == "🕘️更新时间" else original_channel_name,
                        )
                        # 去除一致的，只取第一个频道
                        if processed_channel_name not in exist_channel_name_list:
                            exist_channel_name_list.append(processed_channel_name)
                        else:
                            continue
                        m3u_output += f'#EXTINF:-1 tvg-name="{processed_channel_name}" tvg-logo="https://raw.githubusercontent.com/fanmingming/live/main/tv/{processed_channel_name}.png"'
                        if current_group:
                            m3u_output += f' group-title="{current_group}"'
                        m3u_output += f",{original_channel_name}\n{channel_link}\n"
            m3u_file_path = resource_path(config.jellyfin_file)
            with open(m3u_file_path, "w", encoding="utf-8") as m3u_file:
                m3u_file.write(m3u_output)
            print(f"✅ Save One M3U result file generated at: {m3u_file_path}")


def convert_to_toptvbox_json(first_channel_name=None):
    """
    Convert result txt to json format
    将txt数据转换为top_tv_box的app所需的json文件
    """
    user_final_file = resource_path(config.final_file)
    if os.path.exists(user_final_file):
        with open(user_final_file, "r", encoding="utf-8") as file:
            current_group = None
            update_time = None
            channel_info_map = {}
            index = 0
            for line in file:
                trimmed_line = line.strip()
                if trimmed_line != "":
                    if "#genre#" in trimmed_line:
                        current_group = trimmed_line.replace(",#genre#", "").strip()
                    else:
                        try:
                            original_channel_name, _, channel_link = map(
                                str.strip, trimmed_line.partition(",")
                            )
                        except:
                            continue
                        # 保存更新时间
                        if current_group.find("更新时间") > -1:
                            update_time = original_channel_name
                            continue
                        processed_channel_name = re.sub(
                            r"(CCTV|CETV)-(\d+)(\+.*)?",
                            lambda m: f"{m.group(1)}{m.group(2)}" + ("+" if m.group(3) else ""),
                            first_channel_name if current_group == "🕘️更新时间" else original_channel_name,
                        )
                        logo_url = f"https://raw.githubusercontent.com/fanmingming/live/main/tv/{processed_channel_name}.png"
                        live_url = f"{channel_link}"
                        if processed_channel_name not in channel_info_map:
                            top_tv_box_bean = TopTvBoxDataBean(id=(index := index + 1),
                                                               tvName=processed_channel_name,
                                                               url=live_url,
                                                               category=current_group,
                                                               status=1,
                                                               updateTime=update_time,
                                                               version=update_time,
                                                               logoUrl=logo_url,
                                                               bakUrl=[])
                            channel_info_map[processed_channel_name] = top_tv_box_bean
                        else:
                            top_tv_box_bean = channel_info_map.get(processed_channel_name)
                            top_tv_box_bean.bakUrl.append(live_url)
            channel_info = []
            for result in channel_info_map:
                channel_info.append(channel_info_map.get(result).to_dict())

            data_file_path = resource_path(config.top_tv_box_file)
            if len(channel_info) > 0:
                data = json.dumps(channel_info, indent=4, ensure_ascii=False)
                with open(data_file_path, "w", encoding="utf-8") as data_file:
                    data_file.write(data)
                print(f"✅ TopTvBox result file generated at: {data_file_path}")
                return 1
            else:
                print(f"❌ dont create TopTvBox result file generated at: {data_file_path}, data is null")
                return 0
    return 0


def convert_to_tvbox_json(first_channel_name=None):
    """
    Convert result txt to json format
    将txt数据转换为tv_box的app所需的json文件，带有具体的直播地址
    """
    user_final_file = resource_path(config.final_file)
    if os.path.exists(user_final_file):
        with open(user_final_file, "r", encoding="utf-8") as file:
            current_group = None
            update_time = None
            # 分组信息，key-分组名；value-map(key-频道名称；value-直播地址list)
            group_info_map = {}
            for line in file:
                trimmed_line = line.strip()
                if trimmed_line != "":
                    if "#genre#" in trimmed_line:
                        current_group = trimmed_line.replace(",#genre#", "").strip()
                        group_info_map[current_group] = {}
                    else:
                        try:
                            original_channel_name, _, channel_link = map(
                                str.strip, trimmed_line.partition(",")
                            )
                        except:
                            continue
                        processed_channel_name = re.sub(
                            r"(CCTV|CETV)-(\d+)(\+.*)?",
                            lambda m: f"{m.group(1)}{m.group(2)}" + ("+" if m.group(3) else ""),
                            first_channel_name if current_group == "🕘️更新时间" else original_channel_name,
                        )

                        # 更新时间设定
                        if current_group.find("更新时间") > -1:
                            update_time = original_channel_name

                        live_url = f"{channel_link}"
                        channel_list = group_info_map[current_group].get(processed_channel_name, [])
                        channel_list.append(live_url)
                        group_info_map[current_group][processed_channel_name] = channel_list
            if len(group_info_map) == 0:
                print(f"❌ dont create TvBox result file, data is null")
                return 0
            # 重组tvbox文件
            result = []
            for group in group_info_map:
                channels = []
                for channel in group_info_map[group]:
                    inner_tv_box_data_bean = TvBoxDataBean.InnerTvBoxDataBean(name=channel, urls=group_info_map[group][channel]).to_dict()
                    channels.append(inner_tv_box_data_bean)
                result.append(TvBoxDataBean(group=group, channels=channels, updateTime=update_time).to_dict())

            # 按照tvbox模版文件生成具体的json文件
            demo_file_path = resource_path(config.tv_box_demo_file)
            if not os.path.exists(demo_file_path):
                print(f"❌ dont create TvBox result file, demo file is not exist: {demo_file_path}")
                return 0
            with open(demo_file_path, "r", encoding="utf-8") as file:
                demo_content = file.read()
                if len(demo_content) == 0:
                    print(f"❌ dont create TvBox result file, demo file is empty: {demo_file_path}")
                    return 0
            demo_content_dict = json.loads(demo_content)
            demo_content_dict["lives"] = result
            # 生成json文件
            data_file_path = resource_path(config.tv_box_file)
            if len(result) > 0:
                data = json.dumps(demo_content_dict, indent=4, ensure_ascii=False)
                with open(data_file_path, "w", encoding="utf-8") as data_file:
                    data_file.write(data)
                print(f"✅ TvBox result file generated at: {data_file_path}")
                return 1
            else:
                print(f"❌ dont create TvBox result file generated at: {data_file_path}, data is null")
                return 0
    return 0


def get_result_file_content(show_content=False, file_type=None):
    """
    Get the content of the result file
    """
    user_final_file = resource_path(config.final_file)
    result_file = None
    if os.path.exists(user_final_file):
        if config.open_m3u_result:
            if file_type == "m3u" or not file_type:
                result_file = os.path.splitext(user_final_file)[0] + ".m3u"
            if file_type == "txt":
                result_file = os.path.splitext(user_final_file)[0] + ".txt"
            if file_type == "jellyfin":
                result_file = resource_path(config.jellyfin_file)
            if file_type == "toptvbox":
                result_file = resource_path(config.top_tv_box_file)
            if file_type != "txt" and show_content == False:
                return send_file(result_file, as_attachment=True)
        with open(result_file, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        content = constants.waiting_tip
    response = make_response(content)
    response.mimetype = 'text/plain'
    return response


def remove_duplicates_from_tuple_list(tuple_list, seen, flag=None, force_str=None):
    """
    Remove duplicates from tuple list
    """
    unique_list = []
    for item in tuple_list:
        item_first = item[0]
        part = item_first
        if force_str:
            info = item_first.partition("$")[2]
            if info and info.startswith(force_str):
                continue
        if flag:
            matcher = re.search(flag, item_first)
            if matcher:
                part = matcher.group(1)
        if part not in seen:
            seen.add(part)
            unique_list.append(item)
    return unique_list


def process_nested_dict(data, seen, flag=None, force_str=None):
    """
    Process nested dict
    """
    for key, value in data.items():
        if isinstance(value, dict):
            process_nested_dict(value, seen, flag, force_str)
        elif isinstance(value, list):
            data[key] = remove_duplicates_from_tuple_list(value, seen, flag, force_str)


url_domain_compile = re.compile(
    constants.url_domain_pattern
)


def get_url_domain(url):
    """
    Get the url domain
    """
    matcher = url_domain_compile.search(url)
    if matcher:
        return matcher.group()
    return None


def add_url_info(url, info):
    """
    Add url info to the URL
    """
    if info:
        separator = "-" if "$" in url else "$"
        url += f"{separator}{info}"
    return url


def format_url_with_cache(url, cache=None):
    """
    Format the URL with cache
    """
    cache = cache or get_url_domain(url) or ""
    return add_url_info(url, f"cache:{cache}") if cache else url


def remove_cache_info(string):
    """
    Remove the cache info from the string
    """
    return re.sub(r"[^a-zA-Z\u4e00-\u9fa5$]?cache:.*", "", string)


def resource_path(relative_path, persistent=False):
    """
    Get the resource path
    """
    base_path = os.path.abspath(".")
    total_path = os.path.join(base_path, relative_path)
    if persistent or os.path.exists(total_path):
        return total_path
    else:
        try:
            base_path = sys._MEIPASS
            return os.path.join(base_path, relative_path)
        except Exception:
            return total_path


def write_content_into_txt(content, path=None, position=None, callback=None):
    """
    Write content into txt file
    """
    if not path:
        return

    mode = "r+" if position == "top" else "a"
    with open(path, mode, encoding="utf-8") as f:
        if position == "top":
            existing_content = f.read()
            f.seek(0, 0)
            f.write(f"{content}\n{existing_content}")
        else:
            f.write(content)

    if callback:
        callback()


def get_name_url(content, pattern, multiline=False, check_url=True):
    """
    Get name and url from content
    """
    flag = re.MULTILINE if multiline else 0
    matches = re.findall(pattern, content, flag)
    channels = [
        {"name": match[0].strip(), "url": match[1].strip()}
        for match in matches
        if (check_url and match[1].strip()) or not check_url
    ]
    return channels


def get_real_path(path) -> str:
    """
    Get the real path
    """
    dir_path, file = os.path.split(path)
    user_real_path = os.path.join(dir_path, 'user_' + file)
    real_path = user_real_path if os.path.exists(user_real_path) else path
    return real_path


def get_urls_from_file(path: str) -> list:
    """
    Get the urls from file
    """
    real_path = get_real_path(resource_path(path))
    urls = []
    url_pattern = constants.url_pattern
    if os.path.exists(real_path):
        with open(real_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#"):
                    continue
                match = re.search(url_pattern, line)
                if match:
                    urls.append(match.group().strip())
    return urls


def get_name_urls_from_file(path: str) -> dict[str, list]:
    """
    Get the name and urls from file
    """
    real_path = get_real_path(resource_path(path))
    name_urls = defaultdict(list)
    txt_pattern = constants.txt_pattern
    if os.path.exists(real_path):
        with open(real_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#"):
                    continue
                name_url = get_name_url(line, pattern=txt_pattern)
                if name_url and name_url[0]:
                    name = name_url[0]["name"]
                    url = name_url[0]["url"]
                    if url not in name_urls[name]:
                        name_urls[name].append(url)
    return name_urls


def get_datetime_now():
    """
    Get the datetime now
    """
    now = datetime.datetime.now()
    time_zone = pytz.timezone(config.time_zone)
    return now.astimezone(time_zone).strftime("%Y-%m-%d %H:%M:%S")


def get_version_info():
    """
    Get the version info
    """
    with open(resource_path("version.json"), "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class TopTvBoxDataBean:
    """
    Top TV Box应用定义的bean，包含了与TV盒子相关的基本信息。
    """
    id: Optional[int] = None
    tvName: Optional[str] = None
    url: Optional[str] = None
    bakUrl: List[str] = field(default_factory=list)
    category: Optional[str] = None
    status: int = 1  # 默认为1，表示正常
    updateTime: Optional[datetime] = None
    version: Optional[str] = None
    logoUrl: Optional[str] = None

    @property
    def __repr__(self) -> str:
        return (f"TopTvBoxDataBean(id={self.id}, tvName={self.tvName}, "
                f"url={self.url}, category={self.category}, status={self.status}, "
                f"updateTime={self.updateTime}, version={self.version}, logoUrl={self.logoUrl})")

    def to_dict(self):
        # 使用 __dict__ 来返回对象的所有属性
        return self.__dict__


@dataclass
class TvBoxDataBean:
    """
    TV Box应用定义的bean，包含了与TV盒子相关的基本信息。
    """
    group: Optional[str] = None
    channels: List['TvBoxDataBean.InnerTvBoxDataBean'] = field(default_factory=list)
    epg: Optional[str] = 'http://epg.51zmt.top:8000/api/diyp/'
    updateTime: Optional[str] = None

    def __repr__(self):
        # 生成一个可以用来重新创建对象的字符串
        dict_representation = asdict(self)
        class_name = self.__class__.__name__
        attributes = ', '.join(f'{k}={repr(v)}' for k, v in dict_representation.items())
        return f'{class_name}({attributes})'

    def to_dict(self):
        # 使用 __dict__ 来返回对象的所有属性
        return self.__dict__

    @dataclass
    class InnerTvBoxDataBean:
        name: Optional[str] = None
        urls: List[str] = field(default_factory=list)

        def __repr__(self):
            # 生成一个可以用来重新创建对象的字符串
            class_name = self.__class__.__name__
            attributes = ', '.join(f'{k}={repr(v)}' for k, v in asdict(self).items())
            return f'{class_name}({attributes})'

        def to_dict(self):
            # 使用 __dict__ 来返回对象的所有属性
            return self.__dict__

