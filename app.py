"""Local, stateless HTTP interface for the CT laboratory."""
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from tomography import normalize_image, project, landweber
from uq import experiment
from uq.covariance import geometry
from numpy.linalg import LinAlgError

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024


def integer(value, name, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer from {lower} to {upper}.")
    return value


@app.get('/')
def index():
    return render_template('index.html')


@app.post('/api/<action>')
def calculate(action):
    if action not in ('project', 'reconstruct', 'uq', 'uq-mc'):
        return jsonify(error='Unknown calculation.'), 404
    try:
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        if action in ('uq', 'uq-mc'):
            return jsonify(experiment(body, run_mc=action == 'uq-mc'))
        image = normalize_image(body.get('image'))
        count = integer(body.get('angles'), 'Projection angles', 1, 180)
        iterations = integer(body.get('iterations'), 'Iterations', 1, 1000) if action == 'reconstruct' else None
        matrix, y, angles, offsets, step = project(
            image, count, body.get('noise_std', body.get('sigma', 0.0)), body.get('noise_seed', body.get('seed', 0)))
        step_mode = body.get('step_mode', 'paper')
        if step_mode not in ('paper', 'phase1'):
            raise ValueError('Invalid step size mode.')
        if step_mode == 'paper':
            step = 1 / (2 * geometry(image.shape[0], count)[4][-1])
        if action == 'project':
            return jsonify(sinogram=y.reshape(count, len(offsets)).T.tolist(),
                           offsets=offsets.tolist(), angles=(angles * 180 / 3.141592653589793).tolist(), step=step)
        uncertainty = experiment(body, select_stopping=True)
        iterations = uncertainty['config']['k']
        result = landweber(matrix, y, image.ravel(), iterations, step)
        result['uncertainty'] = uncertainty
        return jsonify(result)
    except MemoryError:
        return jsonify(error='Calculation exceeds available memory. Use the 16 × 16 UQ grid and fewer trials.'), 422
    except LinAlgError:
        return jsonify(error='Spectral calculation did not converge. Try the 16 × 16 UQ grid or a different angle count.'), 422
    except (ValueError, TypeError, BadRequest) as exc:
        return jsonify(error=str(exc)), 400


@app.errorhandler(RequestEntityTooLarge)
def too_large(error):
    return jsonify(error='Image request is too large.'), 413


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, threaded=False)
