"""Kinto to Peertube migration script."""

import csv
import json
import re
import sys
import os
import urllib.request
from base64 import b64encode
from dataclasses import dataclass, asdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List
from urllib.error import HTTPError

import requests
import minicli
from PIL import Image
from progressist import ProgressBar

ROOT = Path(".cache")
KINTO_URL = "https://kinto.classea12.beta.gouv.fr"
KINTO_USER = "classea12admin"
KINTO_PASSWORD = os.environ.get("KINTO_PASSWORD", "")
PEERTUBE_BASE_URL = os.environ.get(
    "PEERTUBE_URL", "https://peertube-staging.classe-a-12.beta.gouv.fr"
)
PEERTUBE_URL = f"{PEERTUBE_BASE_URL}/api/v1"
PEERTUBE_USER = "classea12"
PEERTUBE_PASSWORD = os.environ.get("PEERTUBE_PASSWORD", "")
SCURIOLUS_URL = os.environ.get(
    "SCURIOLUS_URL", "https://files-staging.classe-a-12.beta.gouv.fr"
)


def urlretrieve(url, dest):
    print("Downloading", url)
    bar = ProgressBar(template="Download |{animation}| {done:B}/{total:B}")
    urllib.request.urlretrieve(url, dest, reporthook=bar.on_urlretrieve)


class Mapping:
    """Store remove id of a resource for a given host."""

    storage = ROOT / "mapping.json"

    def __init__(self):
        if not self.storage.exists():
            self.storage.write_text(json.dumps({}))
        self.data = json.loads(self.storage.read_text())
        self.data.setdefault(PEERTUBE_URL, {})

    def __contains__(self, key):
        return key in self.data[PEERTUBE_URL]

    def __getitem__(self, key):
        return self.data[PEERTUBE_URL][key]

    def __setitem__(self, key, value):
        self.data[PEERTUBE_URL][key] = value
        self.write()

    def write(self):
        self.storage.write_text(json.dumps(self.data))


MAPPING = Mapping()


@dataclass
class Attachment:
    filename: str
    hash: str
    location: str
    mimetype: str
    size: int

    @classmethod
    def get_root(self):
        return ROOT / "attachment"

    def download(self, force=False):
        self.get_root().mkdir(exist_ok=True, parents=True)
        dest = self.get_file()
        if not dest.exists() or force:
            urlretrieve(self.location, dest)

    def get_file(self):
        return self.get_root() / self.hash

    def get_filename(self):
        return self.location.split("/")[-1]


@dataclass
class Resource:
    @classmethod
    def get_root(cls):
        return ROOT / cls.__name__.lower()

    @classmethod
    def all(cls):
        for path in cls.get_root().glob("*-meta"):
            yield cls(**json.loads(path.read_text()))

    def persist(self, force=False):
        dest = self.get_root() / f"{self.id}-meta"
        if not dest.exists() or force:
            dest.write_text(json.dumps(asdict(self)))

    def download(self, force=False):
        self.get_root().mkdir(exist_ok=True, parents=True)
        self.persist(force=force)


@dataclass
class WithAttachment(Resource):
    def __post_init__(self):
        if self.attachment:
            self.attachment = Attachment(**self.attachment)

    def download(self, force=False):
        super().download(force=force)
        if self.attachment:
            self.attachment.download(force=force)


@dataclass
class Video(WithAttachment):
    attachment: Attachment
    creation_date: int
    description: str
    duration: int
    grade: str
    id: str
    keywords: List[str]
    last_modified: int
    profile: str
    publish_date: int
    schema: int
    thumbnail: str
    title: str
    quarantine: bool = False

    def __str__(self):
        return self.title

    def download(self, force=False):
        super().download(force=force)
        dest = self.get_root() / f"{self.id}-thumbnail"
        if self.thumbnail and (not dest.exists() or force):
            try:
                urlretrieve(self.thumbnail, dest)
            except HTTPError:
                pass
            else:
                # PeerTube only accepts jpeg.
                Image.open(dest).convert("RGB").save(
                    dest, "JPEG", quality=95, subsampling=0
                )

    def get_thumbnail_file(self):
        return self.get_root() / f"{self.id}-thumbnail"

    def get_thumbnail_filename(self):
        return self.id + ".jpeg"


@dataclass
class Profile(Resource):
    bio: str
    email: str
    name: str
    schema: int
    id: str
    last_modified: int

    @property
    def username(self):
        return self.email.split("@")[0].lower().replace("-", ".")


@dataclass
class Comment(WithAttachment):
    video: str
    schema: int
    comment: str
    profile: str
    id: str
    last_modified: int
    attachment: Attachment = None


@lru_cache()
def get_kinto_headers():
    token = b64encode(f"{KINTO_USER}:{KINTO_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@minicli.cli
def pull_videos(force=False):
    headers = get_kinto_headers()
    resp = requests.get(
        f"{KINTO_URL}/v1/buckets/classea12/collections/videos/records", headers=headers
    )
    videos = resp.json()["data"]
    for raw in videos:
        video = Video(**raw)
        video.download(force=force)
    resp = requests.get(
        f"{KINTO_URL}/v1/buckets/classea12/collections/upcoming/records",
        headers=headers,
    )
    videos = resp.json()["data"]
    for raw in videos:
        video = Video(quarantine=True, **raw)
        video.download(force=force)


@minicli.cli
def pull_comments(force=False):
    resp = requests.get(
        f"{KINTO_URL}/v1/buckets/classea12/collections/comments/records",
        headers=get_kinto_headers(),
    )
    for raw in resp.json()["data"]:
        comment = Comment(**raw)
        comment.download(force=force)


@minicli.cli
def pull_profiles(force=False):
    headers = get_kinto_headers()
    resp = requests.get(f"{KINTO_URL}/v1/accounts", headers=headers)
    if not resp.ok:
        print(resp.text)
        return
    data = resp.json()["data"]
    for raw in data:
        print(f"Pulling {raw['id']}")
        if not raw.get("validated") or not raw.get("profile"):
            print("Skipping.")
            continue
        id_ = raw.get("profile")
        resp = requests.get(
            f"{KINTO_URL}/v1/buckets/classea12/collections/profiles/records/{id_}",
            headers=headers,
        )
        if not resp.ok:
            print(resp.text)
            break
        profile_data = resp.json()["data"]
        profile_data["email"] = raw["id"]
        profile = Profile(**profile_data)
        profile.download(force=force)


@minicli.cli
def pull(force=False):
    pull_videos(force=force)
    pull_profiles(force=force)
    pull_comments(force=force)


@lru_cache()
def get_client():
    resp = requests.get(f"{PEERTUBE_URL}/oauth-clients/local")
    return resp.json()["client_id"], resp.json()["client_secret"]


@lru_cache()
def get_peertube_token(user, password=PEERTUBE_PASSWORD):
    client_id, client_secret = get_client()
    url = f"{PEERTUBE_URL}/users/token"
    resp = requests.post(
        url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "password",
            "response_type": "code",
            "username": user,
            "password": password,
        },
    )
    if not resp.ok:
        print(resp.content)
        sys.exit("Unable to get token")
    return resp.json()["access_token"]


def get_channel_id(headers):
    resp = requests.get(f"{PEERTUBE_URL}/users/me", headers=headers)
    return resp.json()["videoChannels"][0]["id"]


@minicli.cli
def push(limit=1, skip_error=False):
    push_profiles(skip_error, limit)
    push_videos(skip_error, limit)
    push_comments(skip_error, limit)


@minicli.cli
def push_profiles(skip_error=False, limit=1):
    token = get_peertube_token(PEERTUBE_USER)
    headers = {"Authorization": f"Bearer {token}"}
    count = 0
    for profile in Profile.all():
        print(f"Syncing {profile.username}")
        resp = requests.get(f"{PEERTUBE_URL}/accounts/{profile.username}")
        if resp.ok:
            print(f"Profile already in remote server: {profile.username}")
            continue
        data = {
            "email": profile.email.lower(),
            "username": profile.username,
            "password": PEERTUBE_PASSWORD,
            "role": 2,  # User
            "videoQuota": -1,
            "videoQuotaDaily": -1,
        }
        resp = requests.post(f"{PEERTUBE_URL}/users", headers=headers, data=data)
        if not resp.ok:
            print(resp.content)
            if skip_error:
                continue
            breakpoint()
            break
        url = f"{PEERTUBE_URL}/users/me"
        data = {"displayName": profile.name.replace(".", " "), "bio": profile.bio}
        user_token = get_peertube_token(profile.username)
        user_headers = {"Authorization": f"Bearer {user_token}"}
        resp = requests.put(url, headers=user_headers, data=data)
        if not resp.ok:
            print(resp.content)
            if skip_error:
                continue
            breakpoint()
            break
        count += 1
        if limit and count >= limit:
            break


@minicli.cli
def push_comments(skip_error=False, limit=1):
    profiles = {p.id: p for p in Profile.all()}
    admin_token = get_peertube_token(PEERTUBE_USER)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    count = 0
    for comment in sorted(Comment.all(), key=lambda v: v.last_modified):
        video_id = MAPPING[comment.video]
        if comment.id in MAPPING:
            remote_id = MAPPING[comment.id]
            resp = requests.get(
                f"{PEERTUBE_URL}/videos/{video_id}/comment-threads/{remote_id}",
                headers=admin_headers,
            )
            if resp.ok:
                print("Comment already in the remote server", video_id, remote_id)
                continue
        profile = profiles[comment.profile]
        token = get_peertube_token(profile.username)
        headers = {"Authorization": f"Bearer {token}"}
        print(video_id, profile.username)
        url = f"{PEERTUBE_URL}/videos/{video_id}/comment-threads"
        resp = requests.post(url, {"text": comment.comment}, headers=headers)
        if not resp.ok:
            if skip_error:
                continue
            print(resp.content)
            breakpoint()
            break
        remote_id = resp.json()["comment"]["id"]
        print(f"Created comment on video {video_id} as thread {remote_id}")
        MAPPING[comment.id] = remote_id
        if comment.attachment:
            filename = comment.attachment.get_filename()
            path = f"/{video_id}/{remote_id}/{filename}"
            url = f"{SCURIOLUS_URL}{path}"
            resp = requests.put(
                url, data=open(comment.attachment.get_file(), "rb"), headers=headers
            )
            if not resp.ok:
                if skip_error:
                    continue
                print(resp.content)
                breakpoint()
                break
            print(f"Pushed attachment to {url}")
        count += 1
        if limit and count >= limit:
            break


@minicli.cli
def push_videos(skip_error=False, limit=1, video_id=None):
    profiles = {p.username: p for p in Profile.all()}
    ownership = json.loads((ROOT / "mapping_video_user.json").read_text())
    url = f"{PEERTUBE_URL}/videos/upload"
    count = 0
    admin_token = get_peertube_token(PEERTUBE_USER)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    for video in sorted(Video.all(), key=lambda v: v.publish_date):
        if video_id and not video.id == video_id:
            continue
        print(count, video.title, video.id)
        if video.id in MAPPING:
            peertube_uuid = MAPPING[video.id]
            resp = requests.get(
                f"{PEERTUBE_URL}/videos/{peertube_uuid}", headers=admin_headers
            )
            if resp.ok:
                print("Video already in the remote server", peertube_uuid)
                continue
        user = PEERTUBE_USER
        if video.id in ownership:
            username = ownership[video.id]
            profile = profiles.get(username)
            if profile:
                user = username
            elif video.quarantine:
                user = "nicolas.leyri"
            else:
                print(f"Owner not found for {username}")
        print(f"Using user {user}")
        token = get_peertube_token(user)
        headers = {"Authorization": f"Bearer {token}"}
        channel_id = get_channel_id(headers)
        keywords = video.grade.split(" et ") + video.keywords
        data = {
            "name": video.title,
            "channelId": channel_id,
            # PeerTube does not allow empty description.
            "description": video.description or video.title,
            "privacy": 1,
            "tags[]": [k[:30] for k in keywords[:5]],
            "commentsEnabled": True,
            "category": 13,
        }
        if video.publish_date:
            data["originallyPublishedAt"] = datetime.fromtimestamp(
                video.publish_date / 1000
            ).isoformat()
        files = {
            "videofile": (
                video.attachment.get_filename(),
                open(video.attachment.get_file(), "rb"),
                video.attachment.mimetype,
            )
        }
        thumbnail = video.get_thumbnail_file()
        if thumbnail.exists():
            files.update(
                {
                    "previewfile": (
                        video.get_thumbnail_filename(),
                        open(thumbnail, "rb"),
                        "image/jpeg",
                    ),
                    "thumbnailfile": (
                        video.get_thumbnail_filename(),
                        open(thumbnail, "rb"),
                        "image/jpeg",
                    ),
                }
            )
        resp = requests.post(url, headers=headers, files=files, data=data)
        if not resp.ok:
            if skip_error:
                continue
            print(resp.content)
            breakpoint()
            break
        remote_id = resp.json()["video"]["uuid"]
        print(f"Uploaded video as {remote_id}")
        MAPPING[video.id] = remote_id
        # PEERTUBE_USER is admin so its video are never in quarantine.
        if not video.quarantine and user != PEERTUBE_USER:
            print("Removing from quarantine")
            resp = requests.delete(
                f"{PEERTUBE_URL}/videos/{remote_id}/blacklist", headers=admin_headers
            )
            if not resp.ok:
                print(resp.status_code)
                print(resp.content)
        count += 1
        if limit and count >= limit:
            break


@minicli.cli
def process_video_mapping():
    videos = list(Video.all())
    profiles = {p.email: p for p in Profile.all()}
    out = {}
    with Path("video_mapping.csv").open() as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            print(f"Processing {row['name']}")
            if not row["email"]:
                continue
            email = row["email"]
            if email not in profiles:
                print(f"Unkown user {email}")
            for video in videos:
                if clean_string(video.title) == clean_string(row["name"]):
                    out[video.id] = email
                    break
            else:
                print(f"No match found for {row}")
                break
    (ROOT / "mapping_video_user.json").write_text(json.dumps(out))


def clean_string(s):
    return re.sub(r"[^\w]", "", s).lower()


if __name__ == "__main__":
    minicli.run()
