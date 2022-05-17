import multiprocessing

import uvicorn, asyncio, cv2
from vidgear.gears.asyncio import WebGear
from vidgear.gears.asyncio.helper import reducer
from detect import process_frame
from collections import deque
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import os

TOKEN = os.environ.get('VIDEOSERVER_TOKEN', "token")  # token to access this server's drawing functions

PROCESSES = []


def run_server(im, state):
    async def frame_producer():

        while True:
            frame, obj = im[0], im[1]

            frame = await reducer(frame, percentage=30, interpolation=cv2.INTER_AREA)  # reduce frame by 30%

            encodedImage = cv2.imencode(".jpg", frame)[1].tobytes()
            yield (b"--frame\r\nContent-Type:image/jpeg\r\n\r\n" + encodedImage + b"\r\n")
            await asyncio.sleep(0)
        stream.release()

    async def clear_canvas(request: Request):
        data = await request.json()
        if data['token'] != TOKEN:
            return JSONResponse({"status": "error", "message": "Invalid token"})

        state['segments'] = []
        state['cur_points'] = deque(maxlen=512)
        return JSONResponse({"status": "ok"})

    async def start_line(request: Request):
        data = await request.json()
        if data['token'] != TOKEN:
            return JSONResponse({"status": "error", "message": "Invalid token"})

        state['cur_points'] = deque(maxlen=512)
        state['draw_line'] = True
        return JSONResponse({"status": "ok"})

    async def stop_line(request: Request):
        data = await request.json()
        if data['token'] != TOKEN:
            return JSONResponse({"status": "error", "message": "Invalid token"})
        state['segments'] += [state['cur_points']]
        state['cur_points'] = deque(maxlen=512)
        state['draw_line'] = False
        return JSONResponse({"status": "ok"})

    async def get_obj_coords(request: Request):
        data = await request.json()
        if data['token'] != TOKEN:
            return JSONResponse({"status": "error", "message": "Invalid token"})
        return JSONResponse({"status": "ok", "coords": state['obj_coords']})

    web = WebGear(logging=True)

    web.middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]

    web.config["generator"] = frame_producer

    web.routes.append(Route("/clear_canvas", endpoint=clear_canvas, methods=["POST"]))
    web.routes.append(Route("/start_line", endpoint=start_line, methods=["POST"]))
    web.routes.append(Route("/stop_line", endpoint=stop_line, methods=["POST"]))
    web.routes.append(Route("/get_spot_face_coords", endpoint=get_obj_coords, methods=["GET"]))

    uvicorn.run(web(), host="0.0.0.0", port=8000)

    web.shutdown()


def run_camera(im, state):
    stream = cv2.VideoCapture(0)
    stream.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    stream.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    while True:
        (grabbed, frame) = stream.read()
        if not grabbed:
            break

        # frame = await reducer(frame, percentage=30, interpolation=cv2.INTER_AREA)  # reduce frame by 30%

        frame, obj = process_frame(frame)

        if obj:
            state['obj_coords'] = obj.copy()

        if obj and state['draw_line']:
            state['cur_points'].appendleft(obj)

        for segment_points in state['segments'] + [state['cur_points']]:
            for i in range(1, len(segment_points)):
                if segment_points[i - 1] is None or segment_points[i] is None:
                    continue
                cv2.line(frame, segment_points[i - 1], segment_points[i], (0, 0, 255), 2)
        im[0] = frame
        im[1] = obj


def main():
    manager = multiprocessing.Manager()
    lst = manager.list()
    state = manager.dict()

    lst.append(None)
    lst.append(None)

    state['cur_points'] = deque(maxlen=512)
    state['segments'] = []
    state['obj_coords'] = [0, 0]
    state['draw_line'] = False

    server_process = multiprocessing.Process(target=run_server, args=(lst, state))
    camera_process = multiprocessing.Process(target=run_camera, args=(lst, state))

    PROCESSES.append(camera_process)
    PROCESSES.append(server_process)

    for p in PROCESSES:
        p.start()

    for p in PROCESSES:
        p.join()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        for p in PROCESSES:
            p.terminate()
