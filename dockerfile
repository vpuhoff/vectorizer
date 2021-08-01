FROM python:3.9-slim
RUN apt update && apt install g++ python3-dev gcc libc-dev -y
                        
WORKDIR /app/
RUN pip install pipenv
RUN pipenv --python 3
ADD Pipfile /app/Pipfile
ADD Pipfile.lock /app/Pipfile.lock
RUN pipenv install
ADD . /app/
CMD ["pipenv", "run", "python", "main.py"]