import traceback
from app.api.v1.endpoints import google, login
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeSerializer
from app.api.v1.endpoints.google import get_calendar_events
from dotenv import load_dotenv
import boto3
import os
import asyncio
import logging
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

AWS_ACCESS_kEY_ID = os.environ.get("AWS_ACCESS_kEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
dynamodb_client = boto3.resource(
   'dynamodb',
   region_name='ap-northeast-2',
   aws_access_key_id=AWS_ACCESS_kEY_ID,
   aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

def create_dynamodb_data(user_email: str, cal_list: dict) -> dict:
   """
   사용자의 캘린더 리스트를 DynamoDB 형식으로 변환합니다.

   Args:
       user_email (str): 사용자 이메일
       cal_list (dict): Google Calendar API로부터 받은 캘린더 리스트

   Returns:
       dict: DynamoDB 형식으로 변환된 데이터
   """
   new_data = {
       'user_id': user_email,
       'calendar': [
           {
               'id': calendar.get("id", ""),
               'summary': calendar.get("summary", ""),
               'description': calendar.get("description", "")
           }
           for calendar in cal_list.get("items", [])
       ]
   }
   logger.info(f"Transformed data for DynamoDB: {new_data}")
   return new_data

async def get_google_email(access_token: str) -> str:
   """
   Google OAuth2 액세스 토큰을 사용하여 사용자 이메일을 조회합니다.

   Args:
       access_token (str): Google OAuth2 액세스 토큰

   Returns:
       str: 사용자 이메일 또는 None (조회 실패 시)
   """
   url = "https://www.googleapis.com/oauth2/v3/userinfo"
   headers = {"Authorization": f"Bearer {access_token}"}
   
   async with httpx.AsyncClient() as client:
       response = await client.get(url, headers=headers)
       if response.status_code == 200:
           user_info = response.json()
           return user_info.get("email")
       return None

async def put_calendar_list(access_token: str):
   """
   사용자의 Google 캘린더 리스트를 조회하여 DynamoDB에 저장합니다.

   Args:
       access_token (str): Google OAuth2 액세스 토큰
   """
   token_info = {"access_token": access_token}
   cal_data = await google.get_calendar_data(token_info)
   logger.info("Successfully get calendar data")
   
   user_email = await get_google_email(access_token)
   cal_list = create_dynamodb_data(user_email, cal_data)

   try:
       push_to_dynamodb_calendar_list(cal_list)
   except ClientError as e:
       logger.error(f"ClientError: {e.response['Error']['Message']}")
   except Exception as e:
       logger.error(f"Unexpected error: {str(e)}")
       logger.error(f"상세 에러: {traceback.format_exc()}")

async def get_calendar_list_by_user(user_email: str) -> list:
   """
   DynamoDB에서 사용자의 캘린더 리스트를 조회합니다.

   Args:
       user_email (str): 사용자 이메일

   Returns:
       list: 캘린더 리스트 또는 빈 리스트 (조회 실패 시)
   """
   table = dynamodb_client.Table("lookback-calendar-list")
   
   try:
       response = table.get_item(Key={'user_id': user_email})
       return response.get('Item', {}).get('calendar', [])
   except Exception as e:
       logger.error(f"Error getting calendar list from DynamoDB: {str(e)}")
       return []

async def store_calendar_events(user_email: str, access_token: str):
   """
   사용자의 모든 캘린더에 대한 이벤트를 조회하여 DynamoDB에 저장합니다.

   Args:
       user_email (str): 사용자 이메일
       access_token (str): Google OAuth2 액세스 토큰
   """
   calendar_list = await get_calendar_list_by_user(user_email)
   
   for calendar in calendar_list:
       try:
           calendar_id = calendar['id']
           logger.info(f"캘린더 {calendar_id} 이벤트 처리 시작")
           
           events = await get_calendar_events(access_token, [calendar_id])
           if events and events[0]['events']:
               events_data = {
                   'user_id': user_email,
                   'calendar_id': calendar_id,
                   'events': events[0]['events']
               }
               
               await push_to_dynamodb_events(events_data)
               logger.info(f"캘린더 {calendar_id} 이벤트 저장 완료")
           else:
               logger.info(f"캘린더 {calendar_id}에 저장할 이벤트가 없습니다")
       except Exception as e:
           logger.error(f"캘린더 {calendar_id} 처리 중 오류 발생: {str(e)}")
           continue

def push_to_dynamodb_calendar_list(dynamodb_item: dict):
   """
   캘린더 리스트를 DynamoDB에 저장합니다.

   Args:
       dynamodb_item (dict): 저장할 캘린더 리스트 데이터
   """
   table = dynamodb_client.Table("lookback-calendar-list")
   item = {
       "user_id": dynamodb_item["user_id"],
       "calendar": dynamodb_item["calendar"]
   }

   try:
       table.put_item(Item=item)
       logger.info("Successfully inserted item into DynamoDB")
   except Exception as e:
       logger.error(f"Error inserting item into DynamoDB: {str(e)}")

async def push_to_dynamodb_events(events_data: dict):
   """
   캘린더 이벤트를 DynamoDB에 저장합니다.

   Args:
       events_data (dict): 저장할 이벤트 데이터
   """
   table = dynamodb_client.Table("lookback-calendar-events")
   
   try:
       table.put_item(Item=events_data)
       logger.info(f"Successfully stored events for calendar {events_data['calendar_id']}")
   except Exception as e:
       logger.error(f"Error storing events in DynamoDB: {str(e)}")