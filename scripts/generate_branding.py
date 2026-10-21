"""Original project icon; Pillow is needed only to regenerate checked-in assets."""
from pathlib import Path
import base64
import io
import json
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
assets = ROOT/'assets'
assets.mkdir(exist_ok=True)
svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<rect width="256" height="256" rx="58" fill="#17212b"/>
<path d="M184.6 195.4 A88 88 0 1 1 184.6 60.6" fill="none" stroke="#8178ff" stroke-width="22" stroke-linecap="round"/>
<path d="M161.3 175.5 A58 58 0 1 1 161.3 80.5" fill="none" stroke="#c4bfff" stroke-width="13" stroke-linecap="round"/>
<path d="M123 91 L184 128 L123 165 Z" fill="#63e2d2"/>
</svg>
'''
(assets/'app-icon.svg').write_text(svg, encoding='utf-8')
scale = 4
image = Image.new('RGBA', (256*scale, 256*scale))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((0, 0, 256*scale-1, 256*scale-1), radius=58*scale, fill='#17212b')
for box, start, end, width, color in [((40,40,216,216),50,310,22,'#8178ff'),((70,70,186,186),55,305,13,'#c4bfff')]:
    draw.arc(tuple(v*scale for v in box), start, end, fill=color, width=width*scale)
draw.polygon([(123*scale,91*scale),(184*scale,128*scale),(123*scale,165*scale)], fill='#63e2d2')
image = image.resize((256,256), Image.Resampling.LANCZOS)
image.save(assets/'app-icon.png')
image.save(assets/'app-icon.ico', sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
preview = image.resize((48,48), Image.Resampling.LANCZOS)
buffer = io.BytesIO()
preview.save(buffer, format='PNG')
# JSON preserves compatibility with updaters that only accept source/text files.
branding = {'name':'DiscordStreamTunnel','windowIconPNG':base64.b64encode((assets/'app-icon.png').read_bytes()).decode(),
            'headerIconPNG':base64.b64encode(buffer.getvalue()).decode()}
(ROOT/'source/branding.json').write_text(json.dumps(branding, separators=(',',':'))+'\n', encoding='utf-8')
print('Original SVG, PNG, multi-resolution ICO and update-compatible window icons generated.')
