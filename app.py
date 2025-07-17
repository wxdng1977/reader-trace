from flask import Flask, request, jsonify, send_from_directory, Response, stream_template
import os
import json
import uuid
from datetime import datetime
import PyPDF2
import re
from werkzeug.utils import secure_filename
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import threading
import time
import tempfile
import shutil
from werkzeug.exceptions import RequestEntityTooLarge

# 指定静态文件目录为当前目录（包含index.html等）
app = Flask(__name__, static_folder='.', static_url_path='')

# 配置
UPLOAD_FOLDER = 'uploads'
DATA_FOLDER = 'data'
TEMP_FOLDER = 'temp'
ALLOWED_EXTENSIONS = {'pdf', 'epub'}

# 增加文件大小限制到1GB
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB max file size

# 确保必要的文件夹存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

# 存储上传进度
upload_progress = {}

# 添加413错误处理
@app.errorhandler(413)
@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return jsonify({'error': '文件大小超过1GB限制，请上传较小的文件'}), 413


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_file_size_mb(file_path):
    """获取文件大小（MB）"""
    return os.path.getsize(file_path) / (1024 * 1024)

def extract_pdf_contents(pdf_path):
    """提取PDF目录内容"""
    try:
        print(f"开始处理PDF文件: {pdf_path}")
        file_size = get_file_size_mb(pdf_path)
        print(f"PDF文件大小: {file_size:.2f} MB")

        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            total_pages = len(pdf_reader.pages)
            print(f"PDF总页数: {total_pages}")

            # 尝试获取书签/目录
            contents = []
            if pdf_reader.outline:
                print("检测到PDF书签，正在解析...")
                contents = parse_outline(pdf_reader.outline)

            # 如果没有书签，尝试从文本中提取目录
            if not contents:
                print("未检测到书签，尝试从文本中提取目录...")
                contents = extract_contents_from_text(pdf_reader)

            # 如果仍然没有目录，创建页码目录
            if not contents:
                print("创建基于页码的目录...")
                contents = create_page_based_contents(total_pages, 'pdf')

            print(f"提取到 {len(contents)} 个目录项")
            return contents
    except Exception as e:
        print(f"提取PDF目录时出错: {e}")
        return []

def parse_outline(outline, level=0):
    """解析PDF书签"""
    contents = []
    for item in outline:
        if isinstance(item, list):
            contents.extend(parse_outline(item, level + 1))
        else:
            try:
                page_num = item.page.idnum if hasattr(item.page, 'idnum') else 1
                contents.append({
                    'title': item.title,
                    'level': level,
                    'page': page_num,
                    'id': str(uuid.uuid4()),
                    'type': 'chapter'
                })
            except:
                contents.append({
                    'title': item.title,
                    'level': level,
                    'page': 1,
                    'id': str(uuid.uuid4()),
                    'type': 'chapter'
                })
    return contents

def extract_contents_from_text(pdf_reader):
    """从PDF文本中提取可能的目录"""
    contents = []
    total_pages = len(pdf_reader.pages)

    # 对于大文件，只检查前20页
    check_pages = min(20, total_pages)
    print(f"检查前 {check_pages} 页以提取目录")

    # 简单的目录识别模式
    patterns = [
        r'第[一二三四五六七八九十\d]+章\s+(.+)',
        r'第[一二三四五六七八九十\d]+节\s+(.+)',
        r'Chapter\s+\d+\s+(.+)',
        r'Section\s+\d+\s+(.+)',
        r'\d+\.\s+(.+)',
        r'\d+\.\d+\s+(.+)'
    ]

    for page_num in range(check_pages):
        try:
            page = pdf_reader.pages[page_num]
            text = page.extract_text()

            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                for pattern in patterns:
                    match = re.match(pattern, line)
                    if match:
                        # 判断级别
                        level = 0
                        if '章' in line or 'Chapter' in line:
                            level = 0
                        elif '节' in line or 'Section' in line:
                            level = 1
                        elif line.count('.') == 1:
                            level = 1
                        elif line.count('.') == 2:
                            level = 2

                        contents.append({
                            'title': line,
                            'level': level,
                            'page': page_num + 1,
                            'id': str(uuid.uuid4()),
                            'type': 'chapter'
                        })
                        break
        except Exception as e:
            print(f"处理第 {page_num + 1} 页时出错: {e}")
            continue

    return contents

def create_page_based_contents(total_pages, file_type):
    """创建基于页码的目录"""
    contents = []

    # 根据文件类型和页数决定分页策略
    if file_type == 'pdf':
        # PDF文件：根据总页数调整分页策略
        if total_pages <= 50:
            # 小文件：每5页一章
            pages_per_chapter = 5
        elif total_pages <= 200:
            # 中等文件：每10页一章
            pages_per_chapter = 10
        elif total_pages <= 500:
            # 大文件：每20页一章
            pages_per_chapter = 20
        else:
            # 超大文件：每50页一章
            pages_per_chapter = 50
    else:
        # EPUB文件：每5个spine项目为一章
        pages_per_chapter = 5

    chapter_count = 0
    for start_page in range(1, total_pages + 1, pages_per_chapter):
        chapter_count += 1
        end_page = min(start_page + pages_per_chapter - 1, total_pages)

        if start_page == end_page:
            title = f"第{chapter_count}页"
        else:
            title = f"第{chapter_count}章 (第{start_page}-{end_page}页)"

        contents.append({
            'title': title,
            'level': 0,
            'page': start_page,
            'id': str(uuid.uuid4()),
            'type': 'page_range',
            'start_page': start_page,
            'end_page': end_page
        })

    return contents

def extract_epub_contents(epub_path):
    """提取EPUB目录内容"""
    try:
        print(f"开始处理EPUB文件: {epub_path}")
        book = epub.read_epub(epub_path)
        contents = []

        # 尝试从目录中获取章节信息
        toc = book.toc
        if toc:
            print("检测到EPUB目录，正在解析...")
            contents = parse_epub_toc(toc)

        # 如果没有目录，尝试从spine中提取
        if not contents:
            print("未检测到目录，尝试从spine中提取...")
            contents = extract_contents_from_spine(book)

        # 如果仍然没有目录，创建页码目录
        if not contents:
            print("创建基于spine的目录...")
            contents = create_page_based_contents(len(book.spine), 'epub')

        print(f"提取到 {len(contents)} 个目录项")
        return contents
    except Exception as e:
        print(f"提取EPUB目录时出错: {e}")
        return []

def parse_epub_toc(toc, level=0):
    """解析EPUB目录"""
    contents = []
    for item in toc:
        if isinstance(item, tuple):
            # 处理嵌套目录
            link, children = item
            if hasattr(link, 'title') and link.title:
                contents.append({
                    'title': link.title,
                    'level': level,
                    'page': 1,  # EPUB没有页码概念，使用1作为默认值
                    'id': str(uuid.uuid4()),
                    'type': 'chapter'
                })
            if children:
                contents.extend(parse_epub_toc(children, level + 1))
        elif hasattr(item, 'title') and item.title:
            contents.append({
                'title': item.title,
                'level': level,
                'page': 1,
                'id': str(uuid.uuid4()),
                'type': 'chapter'
            })
    return contents

def extract_contents_from_spine(book):
    """从EPUB spine中提取章节信息"""
    contents = []

    for i, item in enumerate(book.spine):
        if hasattr(item, 'id') and item.id in book.toc:
            # 尝试从目录中找到对应的标题
            title = find_title_in_toc(book.toc, item.id)
            if title:
                contents.append({
                    'title': title,
                    'level': 0,
                    'page': i + 1,
                    'id': str(uuid.uuid4()),
                    'type': 'chapter'
                })
            else:
                contents.append({
                    'title': f"第{i + 1}章",
                    'level': 0,
                    'page': i + 1,
                    'id': str(uuid.uuid4()),
                    'type': 'chapter'
                })
        else:
            contents.append({
                'title': f"第{i + 1}章",
                'level': 0,
                'page': i + 1,
                'id': str(uuid.uuid4()),
                'type': 'chapter'
            })

    return contents

def find_title_in_toc(toc, item_id):
    """在目录中查找标题"""
    for item in toc:
        if isinstance(item, tuple):
            link, children = item
            if hasattr(link, 'href') and item_id in link.href:
                return link.title if hasattr(link, 'title') else None
            if children:
                result = find_title_in_toc(children, item_id)
                if result:
                    return result
        elif hasattr(item, 'href') and item_id in item.href:
            return item.title if hasattr(item, 'title') else None
    return None

def extract_document_contents(file_path, file_extension):
    """提取文档目录内容"""
    if file_extension.lower() == 'pdf':
        return extract_pdf_contents(file_path)
    elif file_extension.lower() == 'epub':
        return extract_epub_contents(file_path)
    else:
        return []

def load_document_data(doc_id):
    """加载文档数据"""
    data_file = os.path.join(DATA_FOLDER, f"{doc_id}.json")
    if os.path.exists(data_file):
        with open(data_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_document_data(doc_id, data):
    """保存文档数据"""
    data_file = os.path.join(DATA_FOLDER, f"{doc_id}.json")
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def process_upload_in_background(doc_id, file_path, file_extension, original_filename):
    """在后台处理上传的文件"""
    try:
        print(f"开始后台处理文件: {original_filename}")

        # 更新进度
        upload_progress[doc_id] = {
            'status': 'processing',
            'progress': 0,
            'message': '正在分析文档结构...'
        }

        # 提取目录
        contents = extract_document_contents(file_path, file_extension)

        # 更新进度
        upload_progress[doc_id] = {
            'status': 'processing',
            'progress': 50,
            'message': '正在保存文档信息...'
        }

        # 保存文档信息
        doc_data = {
            'id': doc_id,
            'original_filename': original_filename,
            'file_type': file_extension,
            'upload_time': datetime.now().isoformat(),
            'contents': contents,
            'progress_bars': [],
            'file_size_mb': get_file_size_mb(file_path)
        }

        save_document_data(doc_id, doc_data)

        # 完成处理
        upload_progress[doc_id] = {
            'status': 'completed',
            'progress': 100,
            'message': '文档处理完成！'
        }

        print(f"文件处理完成: {original_filename}")

    except Exception as e:
        print(f"处理文件时出错: {e}")
        upload_progress[doc_id] = {
            'status': 'error',
            'progress': 0,
            'message': f'处理失败: {str(e)}'
        }

@app.route('/')
def index():
    # 返回index.html
    return app.send_static_file('index.html')


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': '没有选择文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if file and allowed_file(file.filename):
        try:
            # 保存原始文件名用于显示
            original_filename = file.filename
            
            # 从原始文件名获取扩展名，避免secure_filename处理中文后的问题
            original_file_parts = original_filename.rsplit('.', 1)
            if len(original_file_parts) < 2:
                return jsonify({'error': '文件名格式错误，缺少文件扩展名'}), 400
            file_extension = original_file_parts[1].lower()
            
            # 验证文件扩展名是否支持
            if file_extension not in ALLOWED_EXTENSIONS:
                return jsonify({'error': f'不支持的文件类型: {file_extension}'}), 400
            
            doc_id = str(uuid.uuid4())
            
            # 使用安全的文件名（仅用于文件系统存储）
            safe_filename = secure_filename(file.filename)
            # 如果secure_filename处理后的文件名没有扩展名，使用原始扩展名
            if '.' not in safe_filename:
                safe_filename = f"{doc_id}.{file_extension}"
            else:
                # 确保使用正确的扩展名
                safe_filename = f"{doc_id}.{file_extension}"

            # 创建临时文件路径
            temp_file_path = os.path.join(TEMP_FOLDER, safe_filename)
            final_file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)

            # 保存文件到临时位置
            file.save(temp_file_path)

            # 检查文件大小
            file_size_mb = get_file_size_mb(temp_file_path)
            print(f"上传文件大小: {file_size_mb:.2f} MB")

            # 更新文件大小限制检查到1GB
            if file_size_mb > 1024:
                os.remove(temp_file_path)
                return jsonify({'error': '文件大小超过1GB限制'}), 400

            # 移动文件到最终位置
            shutil.move(temp_file_path, final_file_path)

            # 初始化上传进度
            upload_progress[doc_id] = {
                'status': 'uploaded',
                'progress': 0,
                'message': '文件上传成功，正在处理...'
            }

            # 在后台线程中处理文件
            thread = threading.Thread(
                target=process_upload_in_background,
                args=(doc_id, final_file_path, file_extension, original_filename)
            )
            thread.daemon = True
            thread.start()

            return jsonify({
                'doc_id': doc_id,
                'filename': original_filename,  # 返回原始文件名
                'file_type': file_extension,
                'file_size_mb': file_size_mb,
                'message': '文件上传成功，正在后台处理...'
            })

        except Exception as e:
            print(f"上传文件时出错: {e}")
            return jsonify({'error': f'上传失败: {str(e)}'}), 500

    return jsonify({'error': '不支持的文件类型'}), 400

@app.route('/upload/progress/<doc_id>')
def get_upload_progress(doc_id):
    """获取上传进度"""
    if doc_id in upload_progress:
        return jsonify(upload_progress[doc_id])
    else:
        return jsonify({'error': '未找到上传进度'}), 404

@app.route('/documents', methods=['GET'])
def get_documents():
    """获取所有文档列表"""
    documents = []
    if os.path.exists(DATA_FOLDER):
        for filename in os.listdir(DATA_FOLDER):
            if filename.endswith('.json'):
                doc_id = filename[:-5]
                doc_data = load_document_data(doc_id)
                if doc_data:
                    documents.append({
                        'id': doc_data['id'],
                        'filename': doc_data['original_filename'],
                        'upload_time': doc_data['upload_time'],
                        'progress_bars_count': len(doc_data.get('progress_bars', [])),
                        'file_size_mb': doc_data.get('file_size_mb', 0)
                    })

    return jsonify(documents)

@app.route('/document/<doc_id>', methods=['GET'])
def get_document(doc_id):
    """获取文档详情"""
    doc_data = load_document_data(doc_id)
    if not doc_data:
        return jsonify({'error': '文档未找到'}), 404

    return jsonify(doc_data)

@app.route('/document/<doc_id>/progress', methods=['POST'])
def create_progress_bar(doc_id):
    """创建进度条"""
    doc_data = load_document_data(doc_id)
    if not doc_data:
        return jsonify({'error': '文档未找到'}), 404

    data = request.json
    progress_bar = {
        'id': str(uuid.uuid4()),
        'name': data.get('name', '默认进度'),
        'start_chapter': data.get('start_chapter', 0),
        'end_chapter': data.get('end_chapter', len(doc_data['contents']) - 1),
        'completed_chapters': [],
        'created_time': datetime.now().isoformat()
    }

    doc_data['progress_bars'].append(progress_bar)
    save_document_data(doc_id, doc_data)

    return jsonify(progress_bar)

@app.route('/document/<doc_id>/progress/<progress_id>', methods=['PUT'])
def update_progress(doc_id, progress_id):
    """更新进度"""
    doc_data = load_document_data(doc_id)
    if not doc_data:
        return jsonify({'error': '文档未找到'}), 404

    data = request.json
    progress_bar = None

    for pb in doc_data['progress_bars']:
        if pb['id'] == progress_id:
            progress_bar = pb
            break

    if not progress_bar:
        return jsonify({'error': '进度条未找到'}), 404

    if 'completed_chapters' in data:
        progress_bar['completed_chapters'] = data['completed_chapters']

    if 'name' in data:
        progress_bar['name'] = data['name']

    if 'start_chapter' in data:
        progress_bar['start_chapter'] = data['start_chapter']

    if 'end_chapter' in data:
        progress_bar['end_chapter'] = data['end_chapter']

    save_document_data(doc_id, doc_data)

    return jsonify(progress_bar)

@app.route('/document/<doc_id>/progress/<progress_id>', methods=['DELETE'])
def delete_progress_bar(doc_id, progress_id):
    """删除进度条"""
    doc_data = load_document_data(doc_id)
    if not doc_data:
        return jsonify({'error': '文档未找到'}), 404

    doc_data['progress_bars'] = [pb for pb in doc_data['progress_bars'] if pb['id'] != progress_id]
    save_document_data(doc_id, doc_data)

    return jsonify({'message': '进度条已删除'})

@app.route('/document/<doc_id>/progress/<progress_id>/toggle/<chapter_id>', methods=['POST'])
def toggle_chapter_progress(doc_id, progress_id, chapter_id):
    """切换章节完成状态"""
    doc_data = load_document_data(doc_id)
    if not doc_data:
        return jsonify({'error': '文档未找到'}), 404

    progress_bar = None
    for pb in doc_data['progress_bars']:
        if pb['id'] == progress_id:
            progress_bar = pb
            break

    if not progress_bar:
        return jsonify({'error': '进度条未找到'}), 404

    if chapter_id in progress_bar['completed_chapters']:
        progress_bar['completed_chapters'].remove(chapter_id)
    else:
        progress_bar['completed_chapters'].append(chapter_id)

    save_document_data(doc_id, doc_data)

    return jsonify(progress_bar)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)