from fastapi import FastAPI, Depends, WebSocket, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import uuid
import asyncio
from manager import manager
from database import SessionLocal, engine, init_db
from models import Base, User, Message
from passlib.context import CryptContext

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

init_db()

# Пароли
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/register")
async def register(username: str, password: str, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    hashed = pwd_context.hash(password)
    user = User(username=username, password_hash=hashed)
    db.add(user)
    db.commit()
    return {"access_token": f"token_{username}"}


@app.post("/login")
async def login(username: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not pwd_context.verify(password, user.password_hash):
        raise HTTPException(status_code=400, detail="Неверные учетные данные")
    return {"access_token": f"token_{username}"}


@app.get("/anonymous")
async def anonymous():
    guest_id = f"guest_{uuid.uuid4().hex[:6]}"
    return {"guest_id": guest_id}


@app.get("/messages")
async def get_messages(db: Session = Depends(get_db)):
    messages = db.query(Message).all()
    return [
        {
            "sender": msg.user.username if msg.user else msg.content.split(": ", 1)[0],
            "message": msg.content,
        }
        for msg in messages
    ]


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = data.split(": ", 1)
            if len(message_data) == 2:
                sender, content = message_data
                user = (
                    db.query(User).filter(User.username == sender).first()
                    if sender
                    else None
                )
                msg = Message(content=data, user_id=user.id if user else None)
                db.add(msg)
                db.commit()
            await manager.broadcast(data)
    except Exception:
        manager.disconnect(websocket)
