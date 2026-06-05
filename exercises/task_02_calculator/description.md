# Task 02 – Calculator

### Objective:

Create a pipeline that will calculate for simple math equations.

### Task Instructions:

1. Add a pipeline parameter named mathOperation and a value of “3+5”.

2. Get the operator from the above parameter and calculate the equation and return the the result in the format below:
[
    {
        "result": "3 + 5 = 8"
    }
]

3. If there is no operator provided return the following response: “No operator in the equation”.
[
    {
        "result": "No operator in the equation"
    }
]

4. Create Triggered task and test it for different equations.
