import os, io, time, shutil
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from PIL import Image
import PyPDF2
import pikepdf

# optional: pdf2image used for aggressive PDF->images conversion if needed
try:
    from pdf2image import convert_from_bytes
    PDF2IMAGE_AVAILABLE = True
except Exception:
    PDF2IMAGE_AVAILABLE = False

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB upload limit

ALLOWED_IMG = {"png", "jpg", "jpeg", "bmp"}
ALLOWED_PDF = {"pdf"}

def allowed(filename, allowed_set):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_set

def save_file(file):
    name = secure_filename(file.filename)
    path = os.path.join(UPLOAD_FOLDER, f"{int(time.time()*1000)}_{name}")
    file.save(path)
    return path

def cleanup_uploads(older_than_seconds=600):
    now = time.time()
    for fn in os.listdir(UPLOAD_FOLDER):
        fp = os.path.join(UPLOAD_FOLDER, fn)
        try:
            if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > older_than_seconds:
                os.remove(fp)
        except Exception:
            pass

@app.route('/')
def home():
    # cleanup in background-ish (simple)
    cleanup_uploads()
    return render_template('index.html')

# Merge PDFs
@app.route('/merge', methods=['POST'])
def merge():
    files = request.files.getlist('pdfs')
    merger = PyPDF2.PdfMerger()
    saved = []
    for f in files:
        if not allowed(f.filename, ALLOWED_PDF): continue
        p = save_file(f); saved.append(p)
        merger.append(p)
    out = os.path.join(UPLOAD_FOLDER, f"merged_{int(time.time()*1000)}.pdf")
    merger.write(out); merger.close()
    return send_file(out, as_attachment=True, download_name="merged.pdf")

# Image(s) -> single PDF
@app.route('/img_to_pdf', methods=['POST'])
def img_to_pdf():
    files = request.files.getlist('images')
    images = []
    for f in files:
        if not allowed(f.filename, ALLOWED_IMG): continue
        im = Image.open(f.stream).convert("RGB")
        images.append(im)
    if not images: return "No images", 400
    out_path = os.path.join(UPLOAD_FOLDER, f"img2pdf_{int(time.time()*1000)}.pdf")
    images[0].save(out_path, save_all=True, append_images=images[1:])
    return send_file(out_path, as_attachment=True, download_name="images_converted.pdf")

# Split PDF by ranges (e.g., "1-3,5")
@app.route('/split', methods=['POST'])
def split():
    file = request.files.get('pdf')
    ranges = request.form.get('ranges','').strip()
    if not file or not allowed(file.filename, ALLOWED_PDF): return "Upload PDF",400
    path = save_file(file)
    reader = PyPDF2.PdfReader(path)
    total = len(reader.pages)
    # parse ranges
    want = []
    for part in [p.strip() for p in ranges.split(",") if p.strip()]:
        if '-' in part:
            a,b = part.split('-',1)
            try:
                a=int(a); b=int(b)
            except: continue
            for i in range(a,b+1):
                if 1<=i<=total: want.append(i-1)
        else:
            try:
                i=int(part)
                if 1<=i<=total: want.append(i-1)
            except: continue
    if not want: return "No pages selected",400
    writer = PyPDF2.PdfWriter()
    for idx in want:
        writer.add_page(reader.pages[idx])
    out = os.path.join(UPLOAD_FOLDER, f"split_{int(time.time()*1000)}.pdf")
    with open(out,"wb") as fh:
        writer.write(fh)
    return send_file(out, as_attachment=True)

# Extract text (plain .txt)
@app.route('/extract', methods=['POST'])
def extract_text():
    file = request.files.get('pdf')
    if not file or not allowed(file.filename, ALLOWED_PDF): return "Upload PDF",400
    path = save_file(file)
    reader = PyPDF2.PdfReader(path)
    parts = []
    for pg in reader.pages:
        try:
            parts.append(pg.extract_text() or "")
        except:
            parts.append("")
    text = "\n\n".join(parts)
    return send_file(io.BytesIO(text.encode('utf-8')), as_attachment=True, download_name="extracted.txt", mimetype="text/plain")

# PDF -> images (zips the images)
@app.route('/pdf_to_images', methods=['POST'])
def pdf_to_images():
    file = request.files.get('pdf')
    fmt = request.form.get('fmt','png').lower()
    if fmt not in {'png','jpg','jpeg'}: fmt='png'
    if not file or not allowed(file.filename, ALLOWED_PDF): return "Upload PDF",400
    path = save_file(file)
    if not PDF2IMAGE_AVAILABLE:
        return jsonify({"error":"pdf2image/poppler not available on server"}),400
    pages = convert_from_bytes(open(path,'rb').read())
    out_files = []
    for i, p in enumerate(pages, start=1):
        outp = os.path.join(UPLOAD_FOLDER, f"page_{i}_{int(time.time()*1000)}.{fmt}")
        p.save(outp, format=fmt.upper())
        out_files.append(outp)
    # If single image, send it; else zip
    if len(out_files)==1:
        return send_file(out_files[0], as_attachment=True)
    else:
        import zipfile
        zip_path = os.path.join(UPLOAD_FOLDER, f"images_{int(time.time()*1000)}.zip")
        with zipfile.ZipFile(zip_path,'w') as zf:
            for f in out_files: zf.write(f, arcname=os.path.basename(f))
        return send_file(zip_path, as_attachment=True, download_name="pages.zip")

# Helpers - compress image to target KB (binary search)
def compress_image_to_target(img: Image.Image, target_kb:int, fmt="JPEG"):
    target_bytes = target_kb*1024
    low,high=10,95
    best=None
    while low<=high:
        mid=(low+high)//2
        buf=io.BytesIO()
        img.save(buf, format=fmt, quality=mid, optimize=True)
        size=buf.tell()
        if size<=target_bytes:
            best=(size,buf)
            low=mid+1
        else:
            high=mid-1
    if best:
        best[1].seek(0)
        return best[1]
    # fallback lowest quality
    buf=io.BytesIO()
    img.save(buf, format=fmt, quality=10, optimize=True)
    buf.seek(0)
    return buf

# Compress to target KB (images or PDFs) - best effort
@app.route('/compress_kb', methods=['POST'])
def compress_kb():
    file = request.files.get('file')
    target_kb = int(request.form.get('target_kb',300))
    if not file: return "Upload file",400
    name = secure_filename(file.filename)
    ext = name.rsplit('.',1)[-1].lower()
    if ext in ALLOWED_IMG:
        img = Image.open(file.stream).convert("RGB")
        buf = compress_image_to_target(img, target_kb, fmt="JPEG")
        return send_file(io.BytesIO(buf.getvalue()), as_attachment=True, download_name=f"compressed_{name}", mimetype="image/jpeg")
    if ext == 'pdf':
        # first try pikepdf optimize
        path = save_file(file)
        out1 = os.path.join(UPLOAD_FOLDER, f"pike_{int(time.time()*1000)}.pdf")
        try:
            pdf = pikepdf.open(path)
            pdf.save(out1, optimize_version=True, compression=pikepdf.CompressionLevel.default)
            pdf.close()
        except Exception:
            out1 = path
        size_kb = os.path.getsize(out1)//1024
        if size_kb <= target_kb:
            return send_file(out1, as_attachment=True)
        # aggressive: rasterize pages -> compress images -> recreate PDF
        if not PDF2IMAGE_AVAILABLE:
            return jsonify({"warning":"Aggressive compression needs pdf2image+poppler on server; returned best-effort pikepdf result.","size_kb":size_kb}), 200
        pages = convert_from_bytes(open(out1,'rb').read())
        per_page = max(30, target_kb // max(1,len(pages)))
        compressed_pages = []
        for p in pages:
            b = compress_image_to_target(p.convert("RGB"), per_page, fmt="JPEG")
            compressed_pages.append(Image.open(b).convert("RGB"))
        final = os.path.join(UPLOAD_FOLDER, f"final_{int(time.time()*1000)}.pdf")
        compressed_pages[0].save(final, save_all=True, append_images=compressed_pages[1:], format="PDF")
        return send_file(final, as_attachment=True)
    return "Unsupported type",400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
