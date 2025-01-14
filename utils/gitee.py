import base64
import datetime
import os
import json

import requests

from utils.config import config
from utils.tools import (
    resource_path
)


def commit_gitee_tvbox_json_file():
    """
    提交tv_box的json文件到gitee
    """
    tv_box_file = resource_path(config.tv_box_file)
    if os.path.exists(tv_box_file):
        with open(tv_box_file, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        return 0
    commit_gitee_file(content, config.gitee_file_sha_url.replace('{file_name}', 'tvbox_lives.json'))


def commit_gitee_tvbox_data_file():
    """
    提交tv_box原始数据文件到gitee
    文件内容为base64，防止gitee和谐
    需要配合特定版本的tvbox版本支持
    """
    final_file = resource_path(config.final_file)
    if os.path.exists(final_file):
        with open(final_file, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        return 0
    # 内容转base64
    content_base64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    commit_gitee_file(content_base64, config.gitee_file_sha_url.replace('{file_name}', 'tv_box_data.txt'))

    # 提交json文件到gitee
    # 按照tvbox模版文件生成具体的json文件
    demo_file_path = resource_path(config.tv_box_demo_file)
    if os.path.exists(demo_file_path):
        with open(demo_file_path, "r", encoding="utf-8") as file:
            demo_content = file.read()
        if len(demo_content) == 0:
            print(f"❌ dont create TvBox result file, demo file is empty: {demo_file_path}")
            return 0
        demo_content_dict = json.loads(demo_content)
        demo_content_dict["lives"] = [{
            "name": "直播",
            "type": 0,
            "url": "https://gitee.com/jujiabao/top-tv-box-config/raw/master/tv_box_data.txt"
        }]
        commit_gitee_file(json.dumps(demo_content_dict, indent=4, ensure_ascii=False), config.gitee_file_sha_url.replace('{file_name}', 'tvbox.json'))


def commit_gitee_toptvbox_file():
    """
    提交top_tv_box文件到gitee
    """
    top_tv_box_file = resource_path(config.top_tv_box_file)
    if os.path.exists(top_tv_box_file):
        with open(top_tv_box_file, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        return 0
    commit_gitee_file(content, config.gitee_file_sha_url.replace('{file_name}', 'data.json'))


def commit_gitee_file(content, gitee_url):
    """
    提交gitee文件和数据
    """
    if len(content) == 0:
        return 0
    # 请求原始文件 SHA
    try:
        response = requests.get(gitee_url, timeout=15)
        response.raise_for_status()
        sha_data = response.json()
        sha = sha_data.get("sha", "")
    except Exception as e:
        print(f"❌ 请求 Gitee 获取文件 SHA 失败: {str(e)}")
        return 0

    if not sha:
        print("❌ 获取到Gitee上的原始文件SHA信息为空，请检查")
        return 0

    # 提交最新的文件信息
    try:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {
            "access_token": config.gitee_access_token,
            "content": base64.b64encode(content.encode('utf-8')).decode('utf-8'),
            "sha": sha,
            "message": f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 提交最新记录"
        }

        response = requests.put(
            gitee_url,
            headers=headers,
            data=payload,
            timeout=15
        )
        response.raise_for_status()
        print(f"✅ Gitee result file commit gitee success: {response.text}")
    except Exception as e:
        print(f"❌ 提交 Gitee 文件更新失败: {str(e)}")
        return 0
