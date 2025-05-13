from fastapi import FastAPI, Depends, WebSocket, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import uuid
import logging
import os
from database import SessionLocal, init_db
from models import User, Message
from manager import manager
from schemas import UserCreate, Token
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Настройка путей
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Статические файлы
app.mount(
    "/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static"
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request):
    return templates.TemplateResponse(
        "started_page.html",
        {"request": request},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        "login_page.html",
        {"request": request},
    )


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse(
        "chat_page.html",  # Создайте этот файл в templates/
        {"request": request},
    )


@app.post("/register")
async def register(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    hashed_password = pwd_context.hash(user.password)
    new_user = User(username=user.username, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    return {"access_token": f"token_{user.username}"}


@app.post("/login")
async def login(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if not db_user or not pwd_context.verify(user.password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect credentials")
    return {"access_token": f"token_{user.username}"}


@app.get("/anonymous")
async def generate_guest_id():
    return {"guest_id": f"guest_{uuid.uuid4().hex[:8]}"}


@app.get("/messages")
async def get_messages(db: Session = Depends(get_db)):
    messages = db.query(Message).order_by(Message.timestamp).all()
    return [
        {
            "sender": msg.sender,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
        }
        for msg in messages
    ]


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if not data.strip():
                continue

            sender = data.split(": ", 1)[0] if ": " in data else "Аноним"
            msg = Message(content=data, sender=sender)
            db.add(msg)
            db.commit()
            await manager.broadcast(data)
    except Exception as e:
        manager.disconnect(websocket)
        logger.error(f"WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)
