"""Anthropic and FAL.ai API clients using direct HTTP requests."""
import io
import json
import os
import requests
import zipfile


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
FAL_BASE = "https://queue.fal.run"


def claude(prompt: str, system: str = "", max_tokens: int = 1500) -> str:
    """Call Claude API and return raw text response."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    messages = [{"role": "user", "content": prompt}]
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        body["system"] = system

    resp = requests.post(
        f"{ANTHROPIC_BASE}/messages",
        headers=headers,
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


def analyze_bio(bio: str) -> dict:
    prompt = f"""You are an expert dating coach who helps people improve their dating profiles.

Analyze this dating profile bio and provide detailed, actionable feedback:

\"\"\"{bio}\"\"\"

Respond with a JSON object (no markdown, just raw JSON) with this exact structure:
{{
  "score": <number 1-10>,
  "scoreExplanation": "<brief explanation of the score>",
  "improvedBio": "<a rewritten version of the bio that is more attractive, authentic, and engaging>",
  "strengths": ["<strength 1>", "<strength 2>"],
  "improvements": ["<improvement 1>", "<improvement 2>", "<improvement 3>"],
  "tips": ["<tip 1>", "<tip 2>", "<tip 3>"],
  "conversationStarters": ["<starter 1>", "<starter 2>", "<starter 3>"]
}}"""

    text = claude(prompt, max_tokens=1500)
    # Strip markdown code blocks if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def rank_photos(photo_descriptions: list) -> dict:
    photo_list = "\n".join(
        f"Photo {i+1}: {d}" for i, d in enumerate(photo_descriptions)
    )
    prompt = f"""You are an expert dating coach helping someone choose the best photos for their dating profile.

The user has uploaded {len(photo_descriptions)} photos. Based on dating app best practices, help them rank and choose the best photos.

Photos:
{photo_list}

Respond with a JSON object (no markdown, just raw JSON):
{{
  "ranking": [<ordered list of photo numbers from best to worst, e.g. [2, 1, 3]>],
  "bestFirstPhoto": <number of the best first photo>,
  "bestFirstPhotoReason": "<why this is the best first photo>",
  "photosToRemove": [<photo numbers to remove>],
  "photosToRemoveReason": "<why to remove these>",
  "missingShotTypes": ["<type of photo missing>"],
  "tips": ["<tip 1>", "<tip 2>", "<tip 3>"]
}}"""

    text = claude(prompt, max_tokens=1000)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def generate_replies(conversation: str, context: str) -> dict:
    prompt = f"""You are an expert dating coach who helps people have engaging, authentic conversations on dating apps.

Context: {context or 'General dating app conversation'}

Conversation:
\"\"\"{conversation}\"\"\"

Generate smart, natural responses. Respond with a JSON object (no markdown, just raw JSON):
{{
  "replies": [
    {{
      "type": "playful",
      "message": "<a playful, flirty reply>",
      "explanation": "<why this works>"
    }},
    {{
      "type": "genuine",
      "message": "<an authentic, genuine reply>",
      "explanation": "<why this works>"
    }},
    {{
      "type": "witty",
      "message": "<a witty, memorable reply>",
      "explanation": "<why this works>"
    }}
  ],
  "conversationTip": "<one tip for keeping the conversation going>",
  "whatToAvoid": "<what not to say in this situation>"
}}"""

    text = claude(prompt, max_tokens=1000)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def generate_first_message(profile: str) -> dict:
    prompt = f"""You are an expert dating coach. Generate engaging first messages for this dating profile:

\"\"\"{profile}\"\"\"

Respond with a JSON object (no markdown, just raw JSON):
{{
  "messages": [
    {{
      "type": "question",
      "message": "<a question based on their profile>",
      "explanation": "<why this works>"
    }},
    {{
      "type": "observation",
      "message": "<an interesting observation about their profile>",
      "explanation": "<why this works>"
    }},
    {{
      "type": "playful",
      "message": "<a playful, fun opener>",
      "explanation": "<why this works>"
    }}
  ],
  "tip": "<one key tip for sending first messages>"
}}"""

    text = claude(prompt, max_tokens=800)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ─────────────────────────────────────────
# FAL.ai photo generation helpers
# ─────────────────────────────────────────

STYLE_PROMPTS = {
    "professional": "PERSONPHOTO, professional headshot portrait, business casual attire, clean background, confident smile, soft studio lighting, photorealistic, 8k quality",
    "casual": "PERSONPHOTO, casual lifestyle photo, relaxed natural smile, outdoor setting, golden hour lighting, photorealistic, 8k quality",
    "travel": "PERSONPHOTO, travel photo at a beautiful scenic location, adventurous happy expression, natural lighting, photorealistic, 8k quality",
    "gym": "PERSONPHOTO, gym fitness photo, athletic wear, confident pose, gym setting, fit healthy look, photorealistic, 8k quality",
    "social": "PERSONPHOTO, social gathering photo, happy outgoing expression, fun setting like a cafe, photorealistic, 8k quality",
}


def fal_create_zip(image_files: list) -> bytes:
    """
    Create an in-memory ZIP archive from a list of (filename, bytes) tuples.
    FAL.ai flux-lora-fast-training requires all training images in a single ZIP.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, data in image_files:
            zf.writestr(filename, data)
    return buf.getvalue()


def fal_submit_training(image_files: list) -> str:
    """
    Submit a LoRA training job to FAL.ai and return request_id.

    Args:
        image_files: list of (filename, bytes) tuples — the training photos.
                     All images are bundled into a ZIP and uploaded to FAL storage
                     before submitting the training job.
    """
    if not FAL_KEY:
        raise ValueError("FAL_KEY is not set")

    # Pack all images into a ZIP and upload it
    zip_bytes = fal_create_zip(image_files)
    zip_url = fal_upload_file(zip_bytes, "training_images.zip", "application/zip")

    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "images_data_url": zip_url,
        "steps": 1000,
        "rank": 16,
        "learning_rate": 0.0004,
        "trigger_word": "PERSONPHOTO",
        "multiresolution_training": True,
    }

    resp = requests.post(
        f"{FAL_BASE}/fal-ai/flux-lora-fast-training",
        headers=headers,
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["request_id"]


def fal_check_status(request_id: str) -> dict:
    """Check FAL.ai training status."""
    headers = {"Authorization": f"Key {FAL_KEY}"}
    resp = requests.get(
        f"{FAL_BASE}/fal-ai/flux-lora-fast-training/requests/{request_id}/status",
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fal_get_result(request_id: str) -> dict:
    """Get FAL.ai training result."""
    headers = {"Authorization": f"Key {FAL_KEY}"}
    resp = requests.get(
        f"{FAL_BASE}/fal-ai/flux-lora-fast-training/requests/{request_id}",
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fal_generate_photo(lora_url: str, style: str) -> list:
    """Generate photos using trained LoRA."""
    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json",
    }
    prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["casual"])

    body = {
        "prompt": prompt,
        "loras": [{"path": lora_url, "scale": 1.0}],
        "num_images": 2,
        "image_size": "portrait_4_3",
        "num_inference_steps": 28,
        "guidance_scale": 3.5,
        "enable_safety_checker": True,
    }

    resp = requests.post(
        "https://fal.run/fal-ai/flux-lora",
        headers=headers,
        json=body,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("images", [])


def fal_upload_file(file_bytes: bytes, filename: str, content_type: str) -> str:
    """Upload a file to FAL storage and return URL."""
    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/octet-stream",
        "X-Fal-File-Name": filename,
    }
    resp = requests.post(
        "https://storage.fal.ai/upload",
        headers=headers,
        data=file_bytes,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["url"]
