import cloudinary
import cloudinary.uploader
import requests
from psd_tools import PSDImage
import svgutils.transform as sg
from telebot.types import Document
from os.path import basename
from telethon.network import connection
import temporary
from os.path import join
from os import remove
import telebot
from tqdm import tqdm
import yaml
from telethon import TelegramClient
from telethon.events import NewMessage
from telethon.tl.custom.message import Message
import asyncio
from telethon.errors.rpcerrorlist import SessionPasswordNeededError
import threading
from time import sleep
import tempfile

download_queue = {}
download_dirs = {}

with open('config.yml') as f:
    config = yaml.safe_load(f)
queue_channel = config['client']['channel_id']



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


def convert_image(source, target, bot, chat_id, message_id):
    psd = PSDImage.open(source)
    with temporary.temp_dir() as temp_dir:
        composition = None
        counter = 0
        count = len(psd)
        for layer in tqdm(psd):
            counter += 1
            bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"Обработка слоя {layer.name} {layer.size} [{counter}/{count}]"
                )

            print(f"Обработка слоя {layer.name}")
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
                status = bot.send_message(
                        chat_id=message.from_user.id,
                        reply_to_message_id=message.id,
                        text="Файл принят в работу"
                    )

                with temporary.temp_dir() as temp_dir:
                    source_file = join(temp_dir, f"{doc.file_id}.psd")
                    if doc.file_size >= 19000000:
                        download_queue[doc.file_name] = None
                        download_dirs[doc.file_name] = str(temp_dir)
                        forwarded_message = bot.forward_message(
                                queue_channel,
                                message.chat.id,
                                message.message_id
                            )
                        print(f"Forwarded message id {message.message_id}")
                        bot.edit_message_text(
                                chat_id=message.chat.id,
                                message_id=status.message_id,
                                text="Файл в очереди на загрузку, загрузка займет некоторое время в зависимости от размера."
                            )
                        for x in tqdm(range(config['client']['download_timeout'])):
                            sleep(1)
                            source_file = download_queue[doc.file_name]
                            if source_file:
                                break
                            else:
                                print(f"waiting download file {doc.file_name}")
                        if not source_file:
                            raise Exception("Не удалось загрузить файл за отведенное время")
                    else:
                        bot.edit_message_text(
                                chat_id=message.chat.id,
                                message_id=status.message_id,
                                text="Загрузка файла"
                            )
                        file_url = bot.get_file_url(file_id=doc.file_id)
                        download_file(file_url, source_file)
                    target_file = join(temp_dir, f"{basename(doc.file_name)}.svg")
                    bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=status.message_id,
                            text="Обработка файла"
                        )
                    convert_image(source_file, target_file, bot, message.chat.id, status.message_id)
                    bot.delete_message(message.chat.id, status.message_id)
                    remove(source_file)
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


async def telegram_loader():
    client = TelegramClient(
        config['client']['entity'],
        config['client']['api_id'],
        config['client']['api_hash'],
        # connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
    )

    @client.on(NewMessage())
    async def new_message_handler(event: NewMessage.Event):
        message: Message = event.message
        doc = message.document
        attr = doc.attributes[0]
        filename = attr.file_name
        target_dir = tempfile.gettempdir()
        target_file = join(target_dir, filename)
        print(f"Client started download {filename}")
        path = await message.download_media(file=target_file)
        download_queue[filename] = path
        print(f"Client received {filename}")
    me = None
    print("Connecting client")
    client.session.set_dc(
        config['client']['dc']['number'],
        config['client']['dc']['ip'],
        config['client']['dc']['port']
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.send_code_request(config['client']['phone'])
        try:
            me = await client.sign_in(
                    phone=config['client']['phone'],
                    code=input('Enter code: '),
                    password=config['client']['password']
                )
        except SessionPasswordNeededError:
            me = await client.sign_in(
                    password=config['client']['password']
                )
        assert me
    else:
        client.start()
    print("Connecting client success")
    while True:
        try:
            # queue = [k for k, v in download_query.items()]
            # if len(queue) == 0:
            #     print("queue is empty")
            await client.run_until_disconnected()
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return "Loader shutdown"
        except Exception as e:
            print(e)

# asyncio.run(telegram_loader())


loop = asyncio.get_event_loop()
task = loop.create_task(telegram_loader())

t = threading.Thread(target=loop.run_until_complete, args=(task,), daemon=True)
t.start()

print("bot loaded")
bot.polling(none_stop=True, interval=1)
