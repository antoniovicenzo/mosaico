"""Synthetic sample reels for a first run without any scraping set up.

They are written to mirror the kinds of claims that circulate in "make money with AI" reels,
including several that the compliance gate should reject. They are clearly marked as samples.
"""

from __future__ import annotations

from .base import Reel

SAMPLES = [
    ("Sell Notion templates", 210_000,
     "I made $3,200 last month selling Notion templates I built with ChatGPT 🤯 Step 1: ask ChatGPT what "
     "students struggle to organise. Step 2: build the template in Notion (free). Step 3: list it on Gumroad "
     "or Etsy for $9-19. Step 4: post TikToks showing the template. Zero investment. Comment NOTION for my guide"),
    ("AI chatbot for local businesses", 95_000,
     "Local dentists, gyms and restaurants will pay you $300-1000 to set up an AI chatbot on their website. "
     "Use a free Voiceflow/Botpress tier, train it on their FAQ page, charge a setup fee + $100/month maintenance. "
     "Find clients on Google Maps and walk in or email them a 2-minute demo video."),
    ("Faceless YouTube channel", 1_300_000,
     "This faceless YouTube channel makes $10k/month and it's 100% AI. ChatGPT writes the script, ElevenLabs does "
     "the voice, CapCut edits. Upload 3 videos a day on any trending topic. Link in bio for the full course 🔥"),
    ("Fake reviews", 40_000,
     "Easy $50/hour: companies pay for 5-star Amazon and Google reviews. Use ChatGPT to write 100 unique reviews "
     "in minutes so they never get detected. DM me 'REVIEWS' to get the client list."),
    ("AI resume rewrites", 61_000,
     "Rewriting resumes with AI is my side hustle: $40-80 per resume on Fiverr. I use ChatGPT with a good prompt "
     "plus a Canva template, takes 30 minutes. Start with 3 friends for testimonials, then post on LinkedIn."),
    ("Essay writing for students", 120_000,
     "Students pay $30 per essay. Have ChatGPT write it, run it through an AI humanizer so Turnitin can't detect it. "
     "Advertise in university Discords. $2k a month easy during finals."),
    ("AI stock images", 300_000,
     "Upload AI images to Adobe Stock and earn every time someone downloads. I generate 50 images a day with free "
     "tools and have 4,000 in my portfolio. Passive income forever! Make sure you tick the 'generative AI' box."),
    ("Trading bot", 2_000_000,
     "This AI trading bot turned $100 into $8,000 in two weeks 🚀 It reads the charts for you. Join my Telegram "
     "for signals, first 50 people get the bot free. Not financial advice 😉"),
    ("Podcast clipping service", 75_000,
     "Podcasters need short clips but hate editing. Use free AI clipping tools (OpusClip free tier) to cut 10 "
     "clips from their latest episode, send them free as a sample, then offer $300/month for weekly clips."),
    ("Mass DM outreach", 33_000,
     "My AI tool sends 500 personalised Instagram DMs a day to business owners while I sleep. 2% reply, 10% of "
     "those buy my $500 offer. Do the math 💸 Use multiple accounts so you don't get banned."),
    ("Kids coloring books on KDP", 480_000,
     "AI coloring books on Amazon KDP. Midjourney makes the pages, Canva makes the cover, KDP prints on demand so "
     "no upfront cost. Some of my books make $400/month each. Niche down: 'dinosaur coloring book for 4 year olds'."),
    ("Prompt packs for real estate agents", 52_000,
     "Sell niche prompt packs: '100 ChatGPT prompts for real estate agents' for $15. Make it in an afternoon, "
     "sell on Gumroad, promote in Facebook groups and on LinkedIn. Niche > generic."),
]


def sample_reels() -> list[Reel]:
    return [
        Reel(
            id=f"seed:{i}",
            source="seed",
            url="",
            author="sample",
            caption=f"[SAMPLE: {title}] {caption}",
            views=views,
            likes=views // 25,
            comments=views // 400,
        )
        for i, (title, views, caption) in enumerate(SAMPLES)
    ]
