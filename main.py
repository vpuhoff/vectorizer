import cloudinary
import cloudinary.uploader
import requests
from psd_tools import PSDImage
import svgutils.transform as sg
from telebot.types import Document
from os.path import basename
import temporary
from os.path import join
import telebot
from tqdm import tqdm
import yaml

with open('config.yml') as f:
    config = yaml.safe_load(f)

DEFAULT_TAG = "vectorizer"
transformations = config['transformations']

cloudinary.config(
  cloud_name=config['cloudinary']['cloud_name'],
  api_key=config['cloudinary']['api_key'],
  api_secret=config['cloudinary']['api_secret']
)

bot_token = config['bot']['token']

bot = telebot.TeleBot(bot_token)


def download_file(url, target_file):
    print(f"Download {url}")
    with requests.get(url, stream=True, timeout=60000) as r:
        r.raise_for_status()
        with open(target_file, 'wb') as f:
            for chunk in tqdm(r.iter_content(chunk_size=8192)):
                f.write(chunk)
    return target_file


def dump_response(response):
    print("Upload response:")
    for key in sorted(response.keys()):
        print("  %s: %s" % (key, response[key]))


def convert_file(source='sample.png', target='target.svg'):
    response = cloudinary.uploader.upload(
        source,
        tags=DEFAULT_TAG,
        public_id="work",
        transformation=transformations,
        format="svg"
    )
    print(f"Converted: {response['asset_id']}")
    download_file(response['url'], target)


def convert_image(source, target):
    psd = PSDImage.open(source)
    with temporary.temp_dir() as temp_dir:
        composition = None
        for layer in tqdm(psd):
            print(layer)
            layer_image = layer.composite()
            layer_file = join(temp_dir, 'work_%s.png' % layer.name)
            layer_vector = join(temp_dir, 'work_%s.svg' % layer.name)
            layer_image.save(layer_file)
            convert_file(layer_file, layer_vector)
            vertorized = sg.fromfile(layer_vector)
            if not composition:
                composition = vertorized
            else:
                composition.append(vertorized.root)
        if composition:
            composition.save(target)
        else:
            raise Exception("No layers found")


@bot.message_handler(content_types=['document'])
def get_text_messages(message: telebot.types.Message):
    try:
        if message.document:
            doc: Document = message.document
            print(f'Received: {doc.file_name}')
            if '.psd' in doc.file_name:
                with temporary.temp_dir() as temp_dir:
                    file_url = bot.get_file_url(file_id=doc.file_id)
                    source_file = join(temp_dir, f"{doc.file_id}.psd")
                    download_file(file_url, source_file)
                    target_file = join(temp_dir, f"{basename(doc.file_name)}.svg")
                    convert_image(source_file, target_file)
                    with open(target_file, 'rb') as td:
                        bot.send_document(
                                chat_id=message.from_user.id,
                                reply_to_message_id=message.id,
                                data=td
                            )
            else:
                bot.send_message(
                        chat_id=message.from_user.id,
                        reply_to_message_id=message.id,
                        text="Файл должен быть в формате PSD"
                    )
    except Exception as e:
        error = f"Не удалось обработать файл по причине {e}"
        if 'file is too big' in error:
            error = "Telegram ограничивает размер файла в 20мб. Невозможно обработать данный файл, потому что он весит больше допустимого."
        bot.send_message(
                        chat_id=message.from_user.id,
                        reply_to_message_id=message.id,
                        text=error
                    )
        print(e)


bot.polling(none_stop=True, interval=0)


# bot.edit_message_text(chat_id=message.chat.id, message_id=message.message_id, text="тру-ту-ту", reply_markup=key )
