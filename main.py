from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from typing import Optional, Dict, List, AsyncGenerator
import logging
import os
import uuid
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext
import json
import asyncio
from starlette.background import BackgroundTask
from contextlib import asynccontextmanager

from database import SessionLocal, init_db
from models import User, Message, Chat, Base
from schemas import UserCreate, ChatCreate, MessageCreate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global dictionary to store connected clients for Server-Sent Events
connected_clients: Dict[int, List[asyncio.Queue]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup
    init_db()

    # Create general chat if it doesn't exist
    with SessionLocal() as db:
        if not db.query(Chat).filter(Chat.id == 1).first():
            general_chat = Chat(id=1, name="General Chat")
            db.add(general_chat)
            db.commit()

    yield

    # Cleanup
    for chat_id in connected_clients:
        for queue in connected_clients[chat_id]:
            await queue.put(None)  # Signal clients to close connections
    connected_clients.clear()


app = FastAPI(lifespan=lifespan)

# Настройки JWT
SECRET_KEY = "your-secret-key-123"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Настройки шаблонов
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount(
    "/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Зависимости
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)
):
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        user = db.query(User).filter(User.username == username).first()
        return user
    except JWTError:
        return None


# Server-Sent Events manager
class EventManager:
    @staticmethod
    async def register(chat_id: int) -> asyncio.Queue:
        """Register a new client connection for a specific chat"""
        if chat_id not in connected_clients:
            connected_clients[chat_id] = []

        # Create a new queue for this client
        queue = asyncio.Queue()
        connected_clients[chat_id].append(queue)
        logger.info(
            f"Client connected to chat {chat_id}. Total clients: {len(connected_clients[chat_id])}"
        )
        return queue

    @staticmethod
    def disconnect(queue: asyncio.Queue, chat_id: int) -> None:
        """Remove a client's queue when they disconnect"""
        if chat_id in connected_clients and queue in connected_clients[chat_id]:
            connected_clients[chat_id].remove(queue)
            logger.info(
                f"Client disconnected from chat {chat_id}. Remaining clients: {len(connected_clients[chat_id])}"
            )

    @staticmethod
    async def broadcast(message: dict, chat_id: int) -> None:
        """Send a message to all connected clients for a specific chat"""
        if chat_id not in connected_clients:
            return

        formatted_message = f"data: {json.dumps(message)}\n\n"
        logger.info(
            f"Broadcasting to {len(connected_clients[chat_id])} clients in chat {chat_id}"
        )

        # Put the message in each client's queue
        for queue in connected_clients[chat_id]:
            await queue.put(formatted_message)


# Create event manager instance
event_manager = EventManager()


# Роуты
@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request):
    return templates.TemplateResponse("started_page.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login_page.html", {"request": request})


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("main_page.html", {"request": request})


@app.post("/register")
async def register(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    hashed = pwd_context.hash(user.password)
    new_user = User(username=user.username, password_hash=hashed)
    db.add(new_user)
    db.commit()

    # Add user to general chat
    general_chat = db.query(Chat).filter(Chat.id == 1).first()
    if general_chat:
        new_user.chats.append(general_chat)
        db.commit()

    return {
        "access_token": create_access_token({"sub": new_user.username}),
        "token_type": "bearer",
        "username": new_user.username,
    }


@app.post("/login")
async def login(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if not db_user or not pwd_context.verify(user.password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    return {
        "access_token": create_access_token({"sub": db_user.username}),
        "token_type": "bearer",
        "username": db_user.username,
    }


@app.get("/anonymous")
async def generate_guest_id():
    return {"guest_id": f"guest_{uuid.uuid4().hex[:8]}"}


@app.post("/chats/")
async def create_chat(
    chat_data: ChatCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    new_chat = Chat(name=chat_data.name)
    db.add(new_chat)
    db.commit()
    db.refresh(new_chat)

    # Add creator to the chat
    current_user.chats.append(new_chat)
    db.commit()

    return new_chat


@app.get("/chats/")
async def get_chats(
    db: Session = Depends(get_db),
):
    # Return all chats for all users
    all_chats = db.query(Chat).all()
    return all_chats


@app.post("/send_message")
async def send_message(
    message: MessageCreate,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    chat = db.query(Chat).filter(Chat.id == message.chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    sender_name = (
        "Anonymous"
        if message.is_anonymous
        else (current_user.username if current_user else "Guest")
    )

    new_message = Message(
        content=message.content,
        chat_id=message.chat_id,
        user_id=current_user.id if current_user else None,
        is_anonymous=message.is_anonymous,
    )
    db.add(new_message)
    db.commit()
    db.refresh(new_message)

    # Broadcast the message to all connected clients for this chat
    await event_manager.broadcast(
        {
            "sender": sender_name,
            "content": message.content,
            "timestamp": new_message.timestamp.isoformat(),
        },
        message.chat_id,
    )

    return {"status": "ok"}


@app.get("/current_user")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    if not current_user:
        return {"username": "Гость"}
    return {"username": current_user.username}


@app.get("/messages")
async def get_messages(chat_id: int = 1, db: Session = Depends(get_db)):
    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.timestamp)
        .all()
    )
    return [
        {
            "sender": (
                "Anonymous"
                if msg.is_anonymous
                else (msg.user.username if msg.user else "Guest")
            ),
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
        }
        for msg in messages
    ]


@app.get("/events")
async def event_stream(request: Request, chat_id: int = 1):
    """SSE endpoint for real-time updates"""

    async def event_generator() -> AsyncGenerator[str, None]:
        # Create a queue for this client
        queue = await event_manager.register(chat_id)

        try:
            # Send a connection established message
            yield 'event: connected\ndata: {"status":"connected","chat_id":' + str(
                chat_id
            ) + "}\n\n"

            # Keep the connection open and yield messages as they come
            while True:
                message = await queue.get()
                if message is None:  # None is our signal to close
                    break
                yield message
        except asyncio.CancelledError:
            # Handle client disconnection
            logger.info("Client disconnected from event stream")
        finally:
            # Clean up when the client disconnects
            event_manager.disconnect(queue, chat_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable buffering for Nginx
        },
    )


# Add user to chat endpoint
@app.post("/chats/{chat_id}/add_user/{username}")
async def add_user_to_chat(
    chat_id: int,
    username: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    user_to_add = db.query(User).filter(User.username == username).first()
    if not user_to_add:
        raise HTTPException(status_code=404, detail="User not found")

    if user_to_add in chat.members:
        return {"status": "User already in chat"}

    chat.members.append(user_to_add)
    db.commit()

    return {"status": "User added to chat"}
