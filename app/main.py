import datetime
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from .hcdp import request_from_params
import os
import json

# Load environment variables from .env file
load_dotenv()

# Get LiteLLM configuration from environment variables
LITELLM_URL = os.getenv("LITELLM_URL")
LITELLM_KEY = os.getenv("LITELLM_KEY")

# Initialize OpenAI client pointing to LiteLLM proxy
client = OpenAI(
    base_url=LITELLM_URL,
    api_key=LITELLM_KEY
)
model = "gpt-5.2-chat"

with open("hcdp_API.txt", "r") as file:
    text = file.read()

app = FastAPI()

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[Message] = []

class ChatResponse(BaseModel):
    response: str


def get_tools():
    """
    Returns tools in OpenAI function calling format.
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_api_parameters",
                "description": "Extract API parameters from user query to request Hawaii climate data",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "time_start": {
                            "type": "string",
                            "description": "Start time for the API request in ISO-8601 format"
                        },
                        "time_end": {
                            "type": "string",
                            "description": "End time for the API request in ISO-8601 format"
                        },
                        "location": {
                            "type": "string",
                            "description": "Location name for the API request"
                        },
                        "lat": {
                            "type": "number",
                            "description": "Latitude for the API request"
                        },
                        "lng": {
                            "type": "number",
                            "description": "Longitude for the API request"
                        },
                        "datatype": {
                            "type": "string",
                            "description": "Variable requested. Rainfall or temperature"
                        },
                        "period": {
                            "type": "string",
                            "description": "Time resolution of the request. Day or month"
                        },
                        "aggregation": {
                            "type": "string",
                            "description": "Aggregation type for temperature. Min, max, mean"
                        }
                    },
                    "required": ["location", "datatype"]
                }
            }
        }
    ]
    
    return tools

# make an endpoint to provide a random interesting fact about the hawaiian island provided by the user
@app.get("/funfact")
async def funfact_endpoint(request: Request):
    """
    Endpoint to provide a random interesting fact about the Hawaiian island provided by the user.
    """
    island = request.query_params.get("island")
    if not island:
        raise HTTPException(status_code=400, detail="Please provide an island name.")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": f"""You are a Hawaii Data Climate Portal AI assistant that provides fun facts about the Hawaiian islands.
Provide a fun fact about {island}. Only return the fact, do not say here is a fun fact.
Provide interesting facts about the island's weather, construction, history, and anything interesting about it.
Make it one sentence long, no more than 20 words, no line breaks."""
                },
                {
                    "role": "user",
                    "content": f"Tell me a fun, interesting fact about {island}."
                }
            ],
            temperature=1.5
        )
        
        if response.choices and response.choices[0].message.content:
            return {"response": response.choices[0].message.content}
        else:
            raise HTTPException(
                status_code=500, detail="Failed to get a valid response from the model."
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """
    Chat endpoint that interacts with GPT-5.2-chat via LiteLLM.
    """
    if not LITELLM_URL or not LITELLM_KEY:
        raise HTTPException(
            status_code=503, detail="LiteLLM integration is not configured."
        )

    try:
        if not request.messages:
            raise HTTPException(
                status_code=400, detail="Please provide at least one message."
            )

        prompt = request.messages[-1].content
        tools = get_tools()
        today = datetime.datetime.now().isoformat()

        # System instruction for parameter extraction
        system_message = {
            "role": "system",
            "content": f"""Today is {today}.
You are a Hawaii Data Climate Portal API assistant to help query the data.
The user will ask questions and your job is to extract the parameters from the user question to generate the request to the Hawaii Data Climate Portal API.
Extract all relevant parameters including time ranges, location, data type, and aggregation."""
        }

        # Build messages for initial request
        messages = [system_message, {"role": "user", "content": prompt}]

        # Make initial request with tools
        initial_response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            temperature=1
        )

        assistant_message = initial_response.choices[0].message
        extra_params = None

        # Check if function call was made
        if assistant_message.tool_calls:
            tool_call = assistant_message.tool_calls[0]
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            
            print(f"Function call detected: {function_name}")
            print(f"Arguments: {function_args}")

            # Call HCDP API with extracted parameters
            hdcp_response = await request_from_params(function_args)
            extra_params = hdcp_response.get("extra_params", None)
            print("HDCP response:", hdcp_response)
            
            if hdcp_response is None:
                raise HTTPException(
                    status_code=500, detail="Failed to get a valid response from the HDCP API."
                )

            # Prepare data summary from HCDP response for second LLM call
            data_dict = hdcp_response.get("data", {})
            data_summary = json.dumps(dict(list(data_dict.items())[-12:]) if data_dict else {}, indent=2)  # Last 12 months

            # Build fresh messages list for analysis call (avoid tool/function roles)
            analysis_messages = [
                {
                    "role": "system",
                    "content": f"""Today is {today}.
You are a Hawaii Data Climate Portal AI assistant that helps users with hawaii climate information.
Analyze the provided climate data and answer the user's question.
Base your answer on the data provided. Use the metric system."""
                },
                {
                    "role": "user",
                    "content": f"""User question: {prompt}

Here is the climate data retrieved from HCDP API (last 12 months):
{data_summary}

Please analyze this data and answer the original question."""
                }
            ]

            # Get final response with context
            final_response = client.chat.completions.create(
                model=model,
                messages=analysis_messages,
                temperature=1
            )

            response_text = final_response.choices[0].message.content

        else:
            # No function call, just use the direct response
            # Build history for general queries
            history_messages = [
                {
                    "role": "system",
                    "content": f"""Today is {today}.
You are a Hawaii Data Climate Portal AI assistant that helps users with hawaii climate information.
Make a comment about the data that answers the user question.
Use the metric system.
Only include your answer, do not include any other text."""
                }
            ]
            
            for msg in request.messages:
                if msg.role == "user":
                    history_messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })

            general_response = client.chat.completions.create(
                model=model,
                messages=history_messages,
                temperature=1
            )

            response_text = general_response.choices[0].message.content

        if response_text:
            return {
                "response": response_text,
                "extra_params": extra_params
            }
        else:
            raise HTTPException(
                status_code=500, detail="Failed to get a valid response from the model."
            )

    except Exception as e:
        print(f"Error in chat endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
