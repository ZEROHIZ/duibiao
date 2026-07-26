#!/usr/bin/env python3
"""Play a video page from start to finish and save HotHook evidence.

This helper uses the user's normal browser when possible, with a persistent
profile for manual login. It does not bypass login, CAPTCHA, paywalls, DRM, or
platform security controls.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import time
from datetime import datetime, timezone

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


WINDOWS_BROWSER_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def default_profile_dir() -> str:
    """Return the stable profile reused across HotHook runs."""
    return os.environ.get(
        "HOTHOOK_PROFILE",
        str(pathlib.Path.home() / ".codex" / "hothook-browser-profile"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play the target video fully and collect HotHook evidence")
    parser.add_argument("url")
    parser.add_argument("--out", default="hothook_full_watch")
    parser.add_argument("--profile", default=default_profile_dir())
    parser.add_argument("--browser", default="auto", choices=("auto", "chromium", "chrome", "msedge"))
    parser.add_argument("--channel", default=None, help="Optional Playwright channel override")
    parser.add_argument("--interval", type=int, default=5, help="Seconds between screenshots while playback runs")
    parser.add_argument("--max-duration", type=int, default=7200, help="Safety cap in seconds")
    parser.add_argument("--require-complete", action="store_true", help="Exit non-zero unless the video reaches the end")
    parser.add_argument("--headless", action="store_true", help="Run headless. Headed mode is safer for login/manual checks")
    return parser.parse_args()


def builtin_browser_executable() -> str | None:
    env_path = os.environ.get("HOTHOOK_BROWSER") or os.environ.get("CHROME_PATH")
    candidates = [env_path] if env_path else []
    candidates.extend(WINDOWS_BROWSER_PATHS)
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).exists():
            return candidate
    return None


def launch_context(playwright, args: argparse.Namespace):
    launch_options = {
        "user_data_dir": args.profile,
        "headless": args.headless,
        "viewport": {"width": 1440, "height": 1000},
        "locale": "zh-CN",
        "args": ["--autoplay-policy=no-user-gesture-required"],
    }
    if args.channel:
        launch_options["channel"] = args.channel
    elif args.browser == "chrome":
        launch_options["channel"] = "chrome"
    elif args.browser == "msedge":
        launch_options["channel"] = "msedge"
    elif args.browser == "auto":
        executable = builtin_browser_executable()
        if executable:
            launch_options["executable_path"] = executable

    try:
        return playwright.chromium.launch_persistent_context(**launch_options)
    except Exception:
        if args.browser != "auto":
            raise
        launch_options.pop("executable_path", None)
        for channel in ("chrome", "msedge"):
            launch_options["channel"] = channel
            try:
                return playwright.chromium.launch_persistent_context(**launch_options)
            except Exception:
                continue
        launch_options.pop("channel", None)
        return playwright.chromium.launch_persistent_context(**launch_options)


def close_common_prompts(page) -> None:
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except Exception:
        pass

    for label in (
        "我知道了",
        "知道了",
        "稍后再说",
        "取消",
        "Accept all",
        "Reject all",
        "I agree",
        "全部接受",
        "拒绝所有内容",
    ):
        try:
            page.get_by_text(label, exact=True).click(timeout=1800)
            print(f"Closed visible prompt: {label}")
            time.sleep(1)
            return
        except Exception:
            pass


def handle_playback_interruptions(page) -> list[dict[str, str]]:
    """Click visible normal playback controls, such as skip-ad and resume buttons."""
    actions: list[dict[str, str]] = []
    button_patterns = (
        r"跳过广告",
        r"略過廣告",
        r"Skip ads?",
        r"Skip Ads?",
        r"Skip Ad",
        r"Skip",
        r"继续播放",
        r"繼續播放",
        r"Continue watching",
        r"Resume",
    )
    for pattern in button_patterns:
        try:
            button = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE)).first
            if button.is_visible(timeout=400):
                button.click(timeout=1200)
                actions.append({"type": "clicked_button", "label": pattern})
                time.sleep(0.8)
                break
        except Exception:
            pass

    try:
        state = video_state(page)
        if state.get("found") and state.get("paused") and not state.get("ended"):
            result = page.evaluate(
                """
                async () => {
                  const video = document.querySelector('video');
                  if (!video) return { ok: false, reason: 'no video element' };
                  try {
                    await video.play();
                    return { ok: true, method: 'video.play' };
                  } catch (error) {
                    return { ok: false, reason: String(error) };
                  }
                }
                """
            )
            if result.get("ok"):
                actions.append({"type": "resumed_playback", "method": str(result.get("method"))})
            else:
                page.keyboard.press("k")
                actions.append({"type": "resume_attempt", "method": "keyboard k"})
            time.sleep(0.8)
    except Exception:
        pass
    return actions


def read_visible_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=8000)
    except Exception:
        return ""


def video_state(page) -> dict[str, object]:
    return page.evaluate(
        """
        () => {
          const video = document.querySelector('video');
          if (!video) return { found: false };
          return {
            found: true,
            currentTime: video.currentTime || 0,
            duration: Number.isFinite(video.duration) ? video.duration : null,
            ended: Boolean(video.ended),
            paused: Boolean(video.paused),
            readyState: video.readyState,
            videoWidth: video.videoWidth || null,
            videoHeight: video.videoHeight || null
          };
        }
        """
    )


def start_playback(page) -> dict[str, object]:
    result = page.evaluate(
        """
        async () => {
          const video = document.querySelector('video');
          if (!video) return { ok: false, reason: 'no video element' };
          video.muted = true;
          video.currentTime = 0;
          try {
            await video.play();
            return { ok: true, method: 'video.play' };
          } catch (error) {
            return { ok: false, reason: String(error) };
          }
        }
        """
    )
    if result.get("ok"):
        return result

    for action in ("Space", "k"):
        try:
            page.keyboard.press(action)
            time.sleep(1)
            state = video_state(page)
            if state.get("found") and not state.get("paused"):
                return {"ok": True, "method": f"keyboard {action}"}
        except Exception:
            pass

    try:
        box = page.locator("video").bounding_box(timeout=3000)
        if box:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            time.sleep(1)
            state = video_state(page)
            if state.get("found") and not state.get("paused"):
                return {"ok": True, "method": "video center click"}
    except Exception:
        pass

    return result


def main() -> int:
    args = parse_args()
    out_dir = pathlib.Path(args.out)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = launch_context(p, args)
        page = context.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            pass

        time.sleep(4)
        close_common_prompts(page)
        time.sleep(2)

        title = page.title()
        initial_text = read_visible_text(page)
        initial_state = video_state(page)
        play_result = start_playback(page)
        start_time = datetime.now(timezone.utc)
        last_progress = float(initial_state.get("currentTime") or 0)
        duration = initial_state.get("duration")
        known_duration = isinstance(duration, (int, float)) and duration > 0
        duration = float(duration) if known_duration else float(args.max_duration)

        manifest: dict[str, object] = {
            "url": page.url,
            "requested_url": args.url,
            "title": title,
            "collected_at": start_time.isoformat(),
            "duration_seconds": duration,
            "interval_seconds": args.interval,
            "started_playback": bool(play_result.get("ok")),
            "playback_start_method": play_result,
            "completed": False,
            "completion_reason": "",
            "frames": [],
            "playback_interruptions": [],
            "note": "Full-playback evidence from visible browser playback. No security bypass attempted.",
        }

        if not play_result.get("ok"):
            manifest["completion_reason"] = f"playback could not start: {play_result.get('reason')}"
        else:
            next_capture_at = 0.0
            stall_started = time.time()
            run_started = time.time()

            while True:
                interruption_actions = handle_playback_interruptions(page)
                if interruption_actions:
                    for action in interruption_actions:
                        action["second"] = str(round(last_progress, 2))
                    interruptions = manifest.get("playback_interruptions")
                    if isinstance(interruptions, list):
                        interruptions.extend(interruption_actions)
                    print(f"Handled playback interruption: {interruption_actions}")

                state = video_state(page)
                current = float(state.get("currentTime") or 0)
                ended = bool(state.get("ended"))
                near_end = known_duration and current >= max(0.0, duration - 1.5)

                if current >= next_capture_at or ended or near_end:
                    frame_name = f"frame_{int(current):04d}.png"
                    frame_path = frames_dir / frame_name
                    try:
                        page.screenshot(path=str(frame_path), full_page=False)
                        manifest["frames"].append(
                            {
                                "second": round(current, 2),
                                "file": str(frame_path),
                                "state": state,
                            }
                        )
                        print(f"Captured {current:07.2f}s -> {frame_name}")
                    except Exception as exc:
                        print(f"Screenshot failed at {current:.2f}s: {exc}")
                    next_capture_at = current + args.interval

                if ended or near_end:
                    manifest["completed"] = True
                    manifest["completion_reason"] = "video ended" if ended else "currentTime reached video duration"
                    break

                if current > last_progress + 0.2:
                    last_progress = current
                    stall_started = time.time()
                elif time.time() - stall_started > 30:
                    manifest["completion_reason"] = "playback stalled for more than 30 seconds"
                    break

                if time.time() - run_started > args.max_duration + 60:
                    manifest["completion_reason"] = "max-duration safety cap reached"
                    break

                time.sleep(1)

        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["final_state"] = video_state(page)
        (out_dir / "full_playback_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "page_text_before_watch.txt").write_text(initial_text, encoding="utf-8")
        (out_dir / "page_text_after_watch.txt").write_text(read_visible_text(page), encoding="utf-8")
        context.close()

    print(f"Saved full-playback evidence to: {out_dir.resolve()}")
    if args.require_complete and not manifest.get("completed"):
        print(f"Playback incomplete: {manifest.get('completion_reason')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
