from flask import Flask, json, request, jsonify, send_file
from flask_cors import CORS
import os
from datetime import datetime
import uuid
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import firebase_admin
from firebase_admin import credentials, storage, firestore
import tempfile
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.lib.units import inch
from io import BytesIO
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFont  # Added imports for logo creation

# Initialize Flask app
app = Flask(__name__)
CORS(app)

firebase_key = os.getenv("FIREBASE_CREDENTIALS")
cred = credentials.Certificate(json.loads(firebase_key))
print(cred)
firebase_admin.initialize_app(cred, {
    'storageBucket': 'national-service-scheme-doc.firebasestorage.app'
})

bucket = storage.bucket()
db = firestore.client()

def validate_program_data(data):
    required_fields = ['name', 'date', 'hours', 'description']
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")
    try:
        hours = float(data['hours'])
        if hours <= 0:
            raise ValueError("Hours must be positive")
    except ValueError:
        raise ValueError("Invalid hours format")
    try:
        datetime.strptime(data['date'], '%Y-%m-%d')
    except ValueError:
        raise ValueError("Invalid date format. Use YYYY-MM-DD")

def upload_to_firebase(file):
    try:
        if not file.filename:
            raise ValueError("Invalid file")
        file.stream.seek(0)
        blob = bucket.blob(f'program_photos/{uuid.uuid4()}_{file.filename}')
        blob.upload_from_file(file.stream, content_type=file.content_type)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        raise Exception(f"Failed to upload file: {str(e)}")

@app.route('/programs', methods=['POST'])
def add_program():
    try:
        validate_program_data(request.form)
        name = request.form['name']
        date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        hours = request.form['hours']
        description = request.form['description']
        files = request.files.getlist('photos')[:4]
        photo_urls = [upload_to_firebase(file) for file in files if file]
        doc_ref = db.collection('programs').document()
        doc_ref.set({
            'name': name,
            'date': date.isoformat(),
            'hours': hours,
            'description': description,
            'photos': photo_urls
        })
        return jsonify({'message': 'Program added successfully', 'id': doc_ref.id}), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/programs')
def get_programs():
    try:
        name = request.args.get('name', '').lower()
        date = request.args.get('date')
        programs_ref = db.collection('programs')
        docs = programs_ref.stream()
        programs = []
        for doc in docs:
            data = doc.to_dict()
            if name and name not in data['name'].lower():
                continue
            if date and date != data['date']:
                continue
            data['id'] = doc.id
            programs.append(data)
        return jsonify(programs), 200
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/programs/export')
def export_pdf():
    try:
        programs_ref = db.collection('programs')
        docs = programs_ref.stream()
        programs = [doc.to_dict() for doc in docs]

        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            file_path = tmp.name
            c = canvas.Canvas(file_path, pagesize=A4)
            width, height = A4

            # Define colors and styles
            primary_color = colors.HexColor('#1a237e')
            header_color = colors.HexColor('#f5f5f5')
            border_color = colors.HexColor('#e0e0e0')
            
            def add_logo(c, x, y, image_url, size=1.5*inch):
                """Add logo to the PDF - either from URL or create dummy"""
                try:
                    if image_url:
                        import requests
                        from urllib3.exceptions import InsecureRequestWarning
                        import warnings
                        warnings.filterwarnings('ignore', category=InsecureRequestWarning)
                        
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                        }
                        response = requests.get(image_url, timeout=15, verify=False, headers=headers)
                        if response.status_code == 200:
                            img_data = response.content
                            img = Image.open(BytesIO(img_data))
                            
                            # Convert RGBA to RGB if necessary
                            if img.mode in ('RGBA', 'LA'):
                                background = Image.new('RGB', img.size, (255, 255, 255))
                                background.paste(img, mask=img.split()[-1])
                                img = background
                            
                            # Save to temporary buffer
                            img_temp = BytesIO()
                            img.save(img_temp, format='PNG')
                            img_temp.seek(0)
                            
                            # Draw with proper scaling
                            img_width = size
                            img_height = size
                            c.drawImage(ImageReader(img_temp), x, y, width=img_width, height=img_height, preserveAspectRatio=True)
                            return True
                    
                    raise Exception("Failed to load image")
                    
                except Exception as e:
                    print(f"Failed to add logo: {str(e)}")
                    # Create dummy logo as fallback
                    img = Image.new('RGB', (100, 100), 'white')
                    draw = ImageDraw.Draw(img)
                    draw.ellipse([0, 0, 99, 99], outline='navy', width=2)
                    try:
                        font = ImageFont.truetype("arial.ttf", 25)
                    except:
                        font = ImageFont.load_default()
                    
                    text = "NSS"
                    bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                    x_text = (100 - text_width) / 2
                    y_text = (100 - text_height) / 2
                    draw.text((x_text, y_text), text, fill='navy', font=font)
                    
                    img_byte_arr = BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    img_byte_arr.seek(0)
                    c.drawImage(ImageReader(img_byte_arr), x, y, width=size, height=size)
                    return False

            def draw_footer(c, page_num):
                if page_num > 1:  # Only on content pages
                    c.saveState()
                    c.setFont('Helvetica', 9)
                    c.setFillColor(colors.grey)
                    
                    # Draw footer text
                    footer_text = "National Service Scheme Unit 191"
                    c.drawCentredString(width/2, 0.5*inch, footer_text)
                    
                    # Draw page number
                    c.drawCentredString(width/2, 0.25*inch, str(page_num))
                    
                    c.restoreState()

            def draw_first_page():
                # Logo configuration - moved lower from top
                logo_size = 1.5*inch  # Fixed size for both logos
                logo_y = height - 2.5*inch  # Moved down from 1.5*inch to 2.5*inch
                
                # Using a CDN-hosted NSS logo
                logo_url2='https://scontent.fcok6-1.fna.fbcdn.net/v/t39.30808-1/306933248_384058177267822_6165528847654932546_n.png?stp=dst-png_s200x200&_nc_cat=104&ccb=1-7&_nc_sid=2d3e12&_nc_ohc=yXLqaNQineIQ7kNvwG3tmVd&_nc_oc=AdnUjs1JAhyw4nwzmR9-9LjgCOBC0yRf1ahK19hBstOFtygD7iZA_k7sShP2EZAykQc&_nc_zt=24&_nc_ht=scontent.fcok6-1.fna&_nc_gid=-bODQ2wA_ko6V8yyLFvE7A&oh=00_AfIk2rwdy2UbYgmvJkLUXFsdMykv1_OoFE2OiWBF6umleg&oe=682767B9'
                logo_url = 'https://yt3.googleusercontent.com/ytc/AIdro_lzHUWs5hpEZDwYhzrGHYS3oqMR1DaFd7KhnIJlpru_ew=s900-c-k-c0x00ffffff-no-rj'
                LOGO_URLS = {
                    'left': logo_url,
                    'right': logo_url2
                }
                
                # Calculate center positions for logos with reduced spacing
                spacing_between_logos = 0.5*inch
                total_width = (2 * logo_size) + spacing_between_logos
                start_x = (width - total_width) / 2
                
                # Draw logos centered with reduced space between them
                # Both logos use the same logo_size to ensure consistent sizing
                add_logo(c, start_x, logo_y, LOGO_URLS['left'], logo_size)  # Left logo
                add_logo(c, start_x + logo_size + spacing_between_logos, logo_y, LOGO_URLS['right'], logo_size)  # Right logo
                
                # Adjust title position relative to new logo position
                c.setFillColor(primary_color)
                c.setFont("Helvetica-Bold", 24)
                title_y = logo_y - 2*inch
                c.drawCentredString(width/2, title_y, "National Service Scheme Unit 191")
                
                # Add footer
                draw_footer(c, 1)
                c.showPage()

            def draw_table(y_position, program, index):
                if y_position < 3*inch:  # Not enough space for table
                    c.showPage()
                    y_position = height - inch

                # Table dimensions
                table_width = width - 2*inch
                x_start = inch
                row_height = 0.4*inch

                # Draw header row with gray background
                c.setFillColor(header_color)
                c.rect(x_start, y_position - row_height, table_width, row_height, fill=1, stroke=0)
                c.setFillColor(primary_color)
                c.setFont("Helvetica-Bold", 12)
                c.drawString(x_start + 0.1*inch, y_position - 0.3*inch, f"{index}. {program['name']}")
                
                # Draw border
                c.setStrokeColor(border_color)
                c.rect(x_start, y_position - row_height, table_width, row_height, fill=0)
                y_position -= row_height

                # Date and Hours row
                date_width = table_width * 0.6
                hours_width = table_width * 0.4
                
                c.setFont("Helvetica", 11)
                c.rect(x_start, y_position - row_height, date_width, row_height, fill=0)
                c.drawString(x_start + 0.1*inch, y_position - 0.3*inch, f"Date: {program['date']}")
                
                c.rect(x_start + date_width, y_position - row_height, hours_width, row_height, fill=0)
                c.drawString(x_start + date_width + 0.1*inch, y_position - 0.3*inch, f"Hours: {program['hours']}")
                y_position -= row_height

                # Description
                desc_style = ParagraphStyle(
                    'desc',
                    fontName='Helvetica',
                    fontSize=11,
                    leading=14
                )
                p = Paragraph(program['description'], desc_style)
                desc_width = table_width - 0.2*inch
                desc_height = p.wrap(desc_width, height)[1] + 0.2*inch
                
                c.rect(x_start, y_position - desc_height, table_width, desc_height, fill=0)
                p.drawOn(c, x_start + 0.1*inch, y_position - desc_height + 0.1*inch)
                
                return y_position - desc_height - 0.2*inch  # Reduced spacing after table

            def draw_images(y_position, photos):
                if not photos:
                    return y_position
                
                # Image dimensions and spacing
                img_width = 2.5*inch
                img_height = 2*inch
                h_spacing = 0.3*inch
                v_spacing = 0.2*inch  # Reduced vertical spacing
                
                # Calculate rows needed
                images_per_row = 2
                rows_needed = (len(photos) + images_per_row - 1) // images_per_row
                total_height = rows_needed * (img_height + v_spacing)
                
                # Check if we need a new page
                if y_position < total_height + inch:
                    c.showPage()
                    y_position = height - inch

                for idx, photo_url in enumerate(photos):
                    try:
                        import requests
                        response = requests.get(photo_url, timeout=10, verify=False)
                        if response.status_code != 200:
                            continue
                            
                        img_data = response.content
                        row = idx // images_per_row
                        col = idx % images_per_row
                        
                        x = inch + col * (img_width + h_spacing)
                        img_y = y_position - row * (img_height + v_spacing) - img_height
                        
                        draw_image(c, img_data, x, img_y, img_width, img_height)
                        
                    except Exception as e:
                        print(f"Failed to process image {idx}: {str(e)}")
                        continue
                
                return y_position - total_height - v_spacing

            # Draw first page
            page_num = 1
            draw_first_page()
            
            # Content pages
            y_position = height - inch
            programs_per_page = 0
            page_num = 2
            
            for idx, program in enumerate(programs, 1):
                if programs_per_page == 2:
                    draw_footer(c, page_num)
                    c.showPage()
                    y_position = height - inch
                    programs_per_page = 0
                    page_num += 1
                
                # Draw table content
                y_position = draw_table(y_position, program, idx)
                
                # Draw images below table
                y_position = draw_images(y_position, program.get('photos', []))
                
                y_position -= 0.3*inch  # Reduced spacing between programs
                programs_per_page += 1
            
            # Add footer to the last page
            draw_footer(c, page_num)
            c.save()

            response = send_file(file_path, as_attachment=True, download_name='nss_documentation.pdf')
            
            @response.call_on_close
            def cleanup():
                if os.path.exists(file_path):
                    os.remove(file_path)
                    
            return response
    except Exception as e:
        return jsonify({'error': 'Failed to generate PDF', 'details': str(e)}), 500

def draw_image(c, img_data, x, y, width, height):
    """Helper function to properly draw images in PDF"""
    try:
        from PIL import Image
        img = Image.open(BytesIO(img_data))
        
        # Convert RGBA to RGB if necessary
        if img.mode in ('RGBA', 'LA'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        
        # Save to temporary buffer
        img_temp = BytesIO()
        img.save(img_temp, format='JPEG')
        img_temp.seek(0)
        
        # Draw in PDF
        c.drawImage(ImageReader(img_temp), x, y, width=width, height=height, preserveAspectRatio=True)
        return True
    except Exception as e:
        print(f"Failed to draw image: {str(e)}")
        return False

@app.route('/programs/<program_id>', methods=['DELETE'])
def delete_program(program_id):
    try:
        # Get the program document reference
        doc_ref = db.collection('programs').document(program_id)
        
        # Check if the document exists
        doc = doc_ref.get()
        if not doc.exists:
            return jsonify({'error': 'Program not found'}), 404
            
        # Get the program data to delete associated photos
        program_data = doc.to_dict()
        
        # Delete photos from Firebase Storage if they exist
        if 'photos' in program_data and program_data['photos']:
            for photo_url in program_data['photos']:
                try:
                    # Extract the path from the URL and handle different URL formats
                    if 'program_photos/' in photo_url:
                        # Get everything after program_photos/
                        file_path = photo_url.split('program_photos/')[1].split('?')[0]  # Remove query parameters if any
                        try:
                            blob = bucket.blob(f'program_photos/{file_path}')
                            blob.delete()
                        except Exception as storage_error:
                            print(f"Failed to delete photo from storage: {str(storage_error)}")
                except Exception as e:
                    print(f"Failed to process photo URL {photo_url}: {str(e)}")
                    continue
        
        # Delete the program document from Firestore
        doc_ref.delete()
        
        return jsonify({
            'message': 'Program and associated photos deleted successfully',
            'program_id': program_id
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Failed to delete program',
            'details': str(e)
        }), 500

if __name__ == '__main__':
    app.run(debug=True)