"""
**Exposer** is a data structure drawing from both <em>histogram</em> and a
<em>scatter plot</em>. Like in <em>histogram</em>, the range of values is
divided into a series of intervals, but like in a <em>scatter plot</em> the
combination of features is analyzed. The rule of bin adjacency is here broken,
so object may fall into more than one of them.

### Usage

To create an _exposer_, all you need is to load a dataset, prepare dictionary
with demanded configuration and use them to initiate object.

    dataset = Dataset('data/iris.csv')
    configuration = {
    'radius': .5,
        'grain': 15,
        'chosenLambda': [2, 3]
    }
    exposer = Exposer(dataset, configuration)

For a process of classification, first is it required to clear supports for all
samples in dataset. Later you can use _exposer_ to create predictions.
Dictionary with scores is provided by a function `score()` being a member of
`dataset` object.

    dataset.clearSupports()
    exposer.predict()
    scores = dataset.score()

"""


from ksskml import Classifier

from enum import Enum

import numpy as np
import math
import operator
import png

"""
### _Exposer_ voting method
_Exposers_ are dedicated to work in classifier ensembles. To establish possible
participations in voting, there is an enum with three possible setups:

- `lone` - don't use weights,
- `theta1` - use single weight for every _exposer_,
- `theta2` - use single weight for every class support coming from _exposer_,
- `theta3` - mix `theta1` and `theta2`,
- `thetas` - mix `theta3` and a saturation value.

"""


class ExposerVotingMethod(Enum):
    lone = 1
    theta1 = 2
    theta2 = 3
    theta3 = 4
    thetas = 5


# === _Exposer_ ===
class Exposer(Classifier):
    # ==== Preparing an _exposer_ ====

    def __init__(self, dataset, configuration):
        Classifier.__init__(self, dataset)
        # First, we're collecting four values from passed configuration:
        #
        # - **voting method**, described above,
        # - **grain**, used as a number of quants in every dimension,
        # - **radius**, used as percentage range of influence generated by
        # every data sample,
        # - **chosen lambda**, a set of features describing the subspace.
        self.exposerVotingMethod = ExposerVotingMethod.lone
        if 'exposerVotingMethod' in configuration:
            self.exposerVotingMethod = configuration['exposerVotingMethod']
        self.grain = configuration['grain']
        self.radius = configuration['radius']
        self.chosenLambda = configuration['chosenLambda']

        # Later, we're calculating number of data structure dimensions, from a
        # number of features of `chosenLambda`.
        self.dimensions = len(self.chosenLambda)

        # To optimize time of positioning in array, we once calculate a vector
        # of consecutive powers of given `grain`.
        self.g = [1] * self.dimensions
        for i in xrange(1, self.dimensions):
            self.g[i] = self.g[i - 1] * self.grain

        # To optimize time of computing a single sample influence, we prepare
        # the set of base move-vectors for given `radius`, with precalculated
        # distance to a central point.
        self.dropVectors = self.dropVectors()

    # === Learning ===
    def learn(self):
        # It gives us enough information to create an empty `matrix` which will
        # store all the information in our _exposer_. Abstraction of
        # n-dimensional array of _pixels_ is realized by the one dimensional
        # list, combined with `position()` function, which will be described
        # later. Pixel here consists of as many values, as we have classes in
        # dataset.
        width = int(math.pow(self.grain, self.dimensions))
        height = len(self.dataset.classes)
        self.model = [[0 for x in range(height)] for y in range(width)]
        self.hsv = [[0 for x in range(3)] for y in range(width)]

        # ==== Exposing array on a beam of samples ====
        for sample in self.dataset.samples:
            # For every `sample` in a `dataset`, we read its `label` and a
            # subset of its `features` for `chosenLambda`.
            label = sample.label
            features = [sample.features[index] for index in self.chosenLambda]

            # Ignore samples with missing values
            if np.isnan(features).any():
                continue

            # According to `features`, we `establish` a `location` of point in
            # exposers space, corresponding to processed `sample`.
            location = np.array(features) * self.grain
            location_i = (location).astype(int)

            # The euclidean distance between quantified (`location_i`) and
            # exact location (`location_f`) lets us to establish a `factor`
            # used to correct distances comming from base vectors.
            distance = [n**2 for n in map(operator.sub, location_i, location)]
            distance = sum(distance)
            distance = math.sqrt(distance)
            factor = 5 - distance

            # Now we can iterate every `dropVector`.
            for dropVector in self.dropVectors:
                # Simple addition between quantified location and a drop vector
                # gives us a real location (`vector`), where the influence will
                # be placed.
                vector = map(operator.add, dropVector[0], location_i)

                # Thus the real location may overflow the space of _exposer_,
                # we need to deal with it by checking if its value fits in
                # range of model.
                overflow = False
                for i in xrange(0, self.dimensions):
                    if vector[i] < 0 or vector[i] >= self.grain:
                        overflow = True
                        continue
                if overflow:
                    continue

                # Finally, we can calculate the real `influence` as a product
                # of drop vector distance from central point and precalculated
                # factor. After calculating its index for single-dimension
                # representation, it is added to matrix at row corresponding
                # to a sample `label`.
                influence = dropVector[1] * factor
                position = self.position(vector)
                self.model[position][label] += influence

        self.normalize()
        self.calculate_measures()

    # === Prediction ===
    def predict(self):
        for sample in self.dataset.test:
            # To predict a class for a `sample` from a test set, we read a
            # subset of its features for chosen lambda and calculate a
            # corresponding location for existing _exposer_.
            features = np.array(
                [sample.features[index] for index in self.chosenLambda])

            # Place .5 instead missing values for prediction.
            features[np.isnan(features)] = .5

            # Establish location
            location = [
                int(feature * self.grain) if feature < 1 else self.grain-1
                for feature in features]

            # Corrected location makes possible to calculate `position` of
            # testing sample in single-dimension representation, which lets us
            # to gather the corresponding `support` vector.
            position = self.position(location)
            support = self.model[position]

            # For the **lone** participation, we simply add the support vector
            # to the support accumulator inside the sample object.
            if self.exposerVotingMethod == ExposerVotingMethod.lone:
                givenSupport = support

            # If it is a **theta1** participation, we increase support
            # accumulator by a product of ensemble support and a scalar measure
            # `theta`.
            elif self.exposerVotingMethod == ExposerVotingMethod.theta1:
                givenSupport = self.theta * np.array(support)

            # When we use **theta2**, a product multiplies ensemble support and
            # a vector measure `thetas`.
            elif self.exposerVotingMethod == ExposerVotingMethod.theta2:
                givenSupport = map(operator.mul, self.thetas, support)

            # When we use **theta3**, a product multiplies ensemble support and
            # both a vector and a scalar `theta` measures.
            elif self.exposerVotingMethod == ExposerVotingMethod.theta3:
                givenSupport = self.theta * \
                    np.array(map(operator.mul, self.thetas, support))

            # When we use **thetas**, a product multiplies ensemble support and
            # a vector measure `thetas`.
            else:
                saturation = self.hsv[position][1]
                givenSupport = saturation * self.theta * \
                    np.array(map(operator.mul, self.thetas, support))

            # Finally, we demand on `sample` to establish a prediction,
            # according to its accumulated support vector.
            sample.support += givenSupport
            sample.decidePrediction()

    # ---

    # === Helpers ===

    # ==== Position calculator ====
    def position(self, p, label=0):
        # Position in `R^n` to `R` transformation is calculated using equation
        # `i(p) = c E(n-1)(k=0 p_k * n^k`,
        acc = 0
        for i in xrange(0, self.dimensions):
            acc += p[i] * self.g[i]
        return acc

    # ==== Drop vectors optimization ====
    def dropVectors(self):
        # As it was said before, to optimize time of calculating single sample
        # influence, we prepare a set of base move-vectors located in given
        # radius around centre point.
        base_vectors = []
        centre = [0] * self.dimensions

        # We need to calculate quantified radius according to percentage radius
        # in given number of quants. It makes possible to calculate a diameter
        # and a beginning (in abstraction, a top left) drop vector position.
        radius = int(self.radius * self.grain)
        diameter = 2 * radius + 1
        beginning = [- radius] * self.dimensions

        # We use two helping vectors (`v` and `z`) to calculate relative
        # position of point.
        v = [-1] * self.dimensions
        z = [1] * self.dimensions

        for i in xrange(1, self.dimensions):
            z[i] = z[i - 1] * diameter

        # And iterate all points in range.
        for i in xrange(0, pow(diameter, self.dimensions)):
            for j in xrange(0, self.dimensions):
                if i % z[j] == 0:
                    v[j] += 1
                if v[j] == diameter:
                    v[j] = 0
            point = map(operator.add, v, beginning)

            # For every point we calculate euclidian distance between it and a
            # centre point.
            distance = math.sqrt(
                sum([n**2 for n in map(operator.sub, centre, point)]))

            # If the distance is lower than quantified radius, we extend the
            # base vector list with a tuple of location and influence, computed
            # as dequantified difference between the two compared values.
            if distance < radius:
                base_vectors.append(
                    (list(point), (radius - distance) / radius))

        return base_vectors

    """
#### Visualization
The visualization of the complete _exposer_, as long as we will have only a
two-dimensional screens at our computers, will be a flat PNG image.

RGB values comes here from first three classes of dataset (if we have a binary
problem, only red and green channel will be populated), combined with a HSV2RGB
conversion, while the axis describes first two dimensions of _exposer_ matrix.

Below we can see an example visualization for the `iris` dataset with chosen
lambda of `[2, 3]` for `1.0` radius and grain of `256` quants.

![](exposer_vis.png)
    """

    def png(self, filename, scale=240):
        image = []
        for y in xrange(0, self.grain):
            row = ()
            vector = [0] * self.dimensions
            vector[1] = y
            for x in xrange(0, self.grain):
                vector[0] = x
                hsv = self.hsv[self.position(vector)]
                support = enumerate(self.model[self.position(vector)])
                rgb = [0] * 3
                for index, value in support:
                    if index > 2:
                        break
                    rgb[index] = value

                h = hsv[0]
                s = hsv[1]
                v = hsv[2]

                c = v * s
                m = v - c
                x = c * (1 - abs((h * 6. % 2) - 1))

                if h < 1. / 6:
                    r, g, b = c, x, 0
                elif h < 2. / 6:
                    r, g, b = x, c, 0
                elif h < 3. / 6:
                    r, g, b = 0, c, x
                elif h < 4. / 6:
                    r, g, b = 0, x, c
                elif h < 5. / 6:
                    r, g, b = x, 0, c
                else:
                    r, g, b = c, 0, x

                row += (
                    rgb[0] * scale + r * (255 - scale),
                    rgb[1] * scale + g * (255 - scale),
                    rgb[2] * scale + b * (255 - scale))
            image += [row]

        f = open(filename, 'wb')
        w = png.Writer(self.grain, self.grain)
        w.write(f, image)
        f.close()

    # ==== Calculating measures ====
    def calculate_measures(self):
        # To establish measures, we calculate HSV representation of color for
        # every sample. Like in classic RGB2HSV computation, V is a maximum
        # value from cone-response vector and S is a product of dividing delta
        # by V. To receive a delta for situations, where we have different
        # number of base colors than three, we are simply dividing index of
        # maximal value by a number of classes.

        treshold = .7
        self.thetas = [0] * len(self.dataset.classes)
        thetas_count = [1] * len(self.dataset.classes)

        presence = np.array([0.] * len(self.dataset.classes))

        for index, pixel in enumerate(self.model):
            cmax = np.max(pixel)
            cmax_i = np.argmax(pixel)
            cmin = np.min(pixel)
            cmin_i = np.argmin(pixel)
            delta = cmax - cmin

            hue = float(cmax_i) / len(self.dataset.classes)

            if hue != 0:
                a = np.array(xrange(1, len(self.dataset.classes) + 1, 1))
                foo = map(operator.mul, a, pixel)
                u = sum(pixel)

            saturation = 0
            if cmax != 0:
                saturation = delta / cmax
            value = cmax

            self.hsv[index] = (hue, saturation, value)

            if value > treshold:
                presence[cmax_i] += 1

        presence /= sum(presence)
        self.thetas = ([1] * len(self.dataset.classes)) - presence

        # And a single measure per _exposer_ is mean value of class measures.
        self.theta = np.mean(self.thetas)

    def normalize(self):
        # ==== Matrix normalization ====

        # We normalize values of each class in range (0,1).
        foo = np.amax(self.model, axis=0)
        self.model /= foo
