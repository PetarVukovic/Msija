from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage


class LLM:
    def __init__(self, model_name: str):
        self.model = ChatGoogleGenerativeAI(model=model_name)

    async def english2translate(self, text: str, language: str) -> str:
        messages = [
            SystemMessage(
                content=f"You are a translator that translates english text into {language}."
            ),
            HumanMessage(content=f"Translate the following text: {text}"),
        ]
        response = await self.model.ainvoke(messages)
        return response.content

    async def croatian2english(self, text: str) -> str:
        messages = [
            SystemMessage(
                content="You are a translator that translates croatian text into english."
            ),
            HumanMessage(content=f"Translate the following text: {text}"),
        ]

        response = await self.model.ainvoke(messages)
        return response.content
