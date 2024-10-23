from pydantic_settings import BaseSettings
from pydantic import BaseModel, model_validator

class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASS: str
    DB_NAME: str

    
    DATABASE_URL: str = None

    SECRET_KEY: str 
    ALGORITHM: str = 'HS256'

    @model_validator(mode='before')
    def get_database_url(cls, values):
        values['DATABASE_URL'] = f'postgresql+asyncpg://{values["DB_USER"]}:{values["DB_PASS"]}@{values["DB_HOST"]}:{values["DB_PORT"]}/{values["DB_NAME"]}'
        return values
    

    REDIS_HOST:str
    REDIS_PORT:int


    class Config: 
        env_file = '.env'

settings = Settings()   
