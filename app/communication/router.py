from fastapi import APIRouter, Request, Form, Response, Depends, requests

from fastapi.templating import Jinja2Templates
from typing import List, Dict
import json
from redis import asyncio as aioredis
import json
import asyncio
from datetime import datetime
from sqlalchemy import select, insert, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession 

from app.communication.dao import UsersDAO, MessagesDAO

from fastapi.responses import RedirectResponse

from app.models import Users, Messages

from app.database import async_session_maker


from app.communication.auth import get_password_hash, authenticate_user, create_access_token
from app.communication.dependencies import get_token, get_current_user 

from fastapi import WebSocket, WebSocketDisconnect

import asyncio


from app.tasks.tasks import send_msg_task


router = APIRouter(
    tags=['Фронтэнд']
)




templates = Jinja2Templates(directory='app/templates')



class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)



manager = ConnectionManager()


async def send_msg(chat_id, text): 
    send_msg_task.delay(chat_id, text)  # Отправка задачи в очередь




@router.websocket("/ws/{user_id}/{other_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int, other_id: int):
    await manager.connect(websocket)
    redis = await get_redis()
    
    try:
        while True:
            
            message = await websocket.receive_text()

            user_name = await UsersDAO.get_user_name_by_id(user_id)
            cache_key = f"messages:{user_id}:{other_id}" if user_id < other_id else f"messages:{other_id}:{user_id}"
            cached_messages = await redis.get(cache_key)
            if cached_messages:
                messages = json.loads(cached_messages)
                
                messages.append((message, user_name, datetime.now().strftime("%H:%M")))
                await redis.set(cache_key, json.dumps(messages), ex=600) 
            else:
                messages = []
                messages.append((message, user_name, datetime.now().strftime("%H:%M")))
                await redis.set(cache_key, json.dumps(messages), ex=600) 

            await MessagesDAO.put_message_to_db(sender_id=user_id, recipient_id=other_id, message=message)


            telegram_message = f'Вам пришло сообщение от {user_name}. Сообщение:{message}'

            #беру id 
            async with async_session_maker() as session: 
                telegram_id_result = await session.execute(select(Users.telegram_id).filter(Users.id == other_id))
                telegram_id = telegram_id_result.scalars().first()

            #беру токен
            async with async_session_maker() as session: 
                token_result = await session.execute(select(Users.token).filter(Users.id == other_id))
                check_token = token_result.scalars().first()

            #проверка что есть id и id не равно 0, + проверка что токен не активен (то есть пользвателя нет в сети) 
            if telegram_id and telegram_id != 0: 
                if check_token == 0:
                    #если id != 0 и токен = 0 (то есть не активен) то отправляю сообщение в телеграм
                    await send_msg(telegram_id, telegram_message)

            await manager.broadcast(json.dumps(f'{user_name}: {datetime.now().strftime("%H:%M")} - {message}'))

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast(f"User {user_id} left the chat")
    except Exception as e:
        print(e)






@router.get("/main_page")
async def start_page(request: Request, user: Users = Depends(get_current_user)):
    conversation_users = await MessagesDAO.get_conversation_users(user_id=user.id)

    return templates.TemplateResponse("start_page.html", {"request": request, "conversation_users": conversation_users})
 


async def get_redis():
    redis = aioredis.from_url("redis://cache:6379")
    return redis


@router.get("/messages/{other_user_id}") 
async def get_messages(request: Request, other_user_id: int, user: Users = Depends(get_current_user)):  

    user_name = await UsersDAO.get_user_name_by_id(user.id)
    other_user_name = await UsersDAO.get_user_name_by_id(other_user_id)
    

    redis = await get_redis()

    cache_key = f"messages:{user.id}:{other_user_id}" if user.id < other_user_id else f"messages:{other_user_id}:{user.id}"
    cached_messages = await redis.get(cache_key)

    if cached_messages:
        messages = json.loads(cached_messages)
    else:
        messages = await MessagesDAO.get_messages_between_users(user_id=user.id, other_user_id=other_user_id)

        await redis.set(cache_key, json.dumps(messages), ex=600) 

    return templates.TemplateResponse("message_page.html", { 
        "request": request, 
        "messages": messages,
        'user_name' : user_name, 
        "other_name": other_user_name,  # Отправляем id другого пользователя 
        'user_id' : user.id,
        'other_id': other_user_id
    })









@router.post("/send_message") 
async def send_message(recipient_id: int = Form(...), message: str = Form(...), user: Users = Depends(get_current_user)): 
    await MessagesDAO.put_message_to_db(sender_id=user.id, recipient_id=recipient_id, message=message) 
    return RedirectResponse(url=f"/messages/{recipient_id}", status_code=303)




@router.get("/all_users")
async def all_users(request: Request, user: Users = Depends(get_current_user)):

    async with AsyncSession() as session: 
        async with session.begin():
            users = await UsersDAO.find_without_me(user.id)  
                
            return templates.TemplateResponse("users.html", {"request": request, "users": users})
        




@router.get("/") 
async def login_form(request: Request, error_message: str = None):
    return templates.TemplateResponse("register_and_auth.html", {"request": request, "error_message": error_message})




@router.post("/")
async def login_user(request: Request, response: Response, user_name: str = Form(...), password: str = Form(...)):
    # получаем пользователя
    user = await authenticate_user(user_name, password)
    if not user:
        # Возвращаем форму входа с сообщением об ошибке 
        return templates.TemplateResponse("register_and_auth.html", { 
            "request": request,  
            "error_message": "Неправильный логин или пароль", 
        })
        
    access_token = create_access_token({'sub': str(user.id)}) 

    await UsersDAO.update_token(user.id, 1)


    response = RedirectResponse(url='/main_page', status_code=303)
    response.set_cookie('booking_access_token', access_token, httponly=True)


    return response







@router.post("/register") 
async def register_user(request: Request, user_name: str = Form(...), password: str = Form(...), 
                        password_repeat: str = Form(...), 
): 
    
    async with AsyncSession() as session: 
        async with session.begin():
    # смотрим, есть ли такой юзер. если есть - вернем ошибку 
            existing_user = await UsersDAO.find_one_or_none(user_name=user_name)
            if existing_user:
                return templates.TemplateResponse("register_and_auth.html", { 
                    "request": request,  
                    "error_message": "Пользователь с таким user_name уже существует" 
                })


        if password != password_repeat: 
            return templates.TemplateResponse("register_and_auth.html", { 
                "request": request, 
                "error_message": "Пароли не совпадают" 
            }) 

        hashed_password = get_password_hash(password) 

        await UsersDAO.add(user_name=user_name, password=hashed_password, telegram_id=0) 


        return templates.TemplateResponse("register_and_auth.html", { 
            "request": request, 
            "success_message": "Вы успешно зарегистрированы!" 
        })



@router.post('/logout') #response_class=HTMLResponse
async def logout_user(response: Response, request: Request): 

    token = get_token(request)  # Получаем токен, если он существует 
    user = await get_current_user(token)  # Получаем текущего пользователя по токену

    # Устанавливаем значение токена в базе данных для пользователя на 0 
    await UsersDAO.update_token(user.id, 0)

    response = RedirectResponse(url='/', status_code=303) 
    response.delete_cookie('booking_access_token')
    return response
    