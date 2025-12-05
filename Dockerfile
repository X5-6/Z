FROM python:3.10-slim

# Set a working directory
WORKDIR /app

# Copy project files
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port used by keep_alive
ENV PORT=8080

VOLUME ["/data"]

CMD ["python","main.py"]
